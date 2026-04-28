from typing import Any, ClassVar
from datetime import datetime

import numpy as np
import pandas as pd
import pyomo.environ as pyomo
from attrs import field, define

from h2integrate.core.utilities import merge_shared_inputs, build_time_series_from_plant_config
from h2integrate.core.validators import range_val, has_required_keys
from h2integrate.control.control_strategies.controller_opt_problem_state import DispatchProblemState
from h2integrate.control.control_strategies.pyomo_storage_controller_baseclass import (
    SolverOptions,
    PyomoStorageControllerBaseClass,
    PyomoStorageControllerBaseConfig,
)


@define
class PeakLoadManagementOptimizedControllerConfig(PyomoStorageControllerBaseConfig):
    """Configuration for the PLM DR optimized storage controller.

    Inherits base fields from ``PyomoStorageControllerBaseConfig``:
    ``max_capacity``, ``max_soc_fraction``, ``min_soc_fraction``,
    ``init_soc_fraction``, ``n_control_window``, ``commodity``,
    ``commodity_rate_units``, ``tech_name``,
    ``system_commodity_interface_limit``, ``round_digits``.

    Attributes:
        max_charge_rate (float): Maximum charge and discharge rate (kW).
        supervisory_signal (list[float]): Price, demand, or price*demand
            forecast time series. The rolling solver uses one window of
            length ``n_control_window`` per solve.
        peak_window (dict): Hours eligible for dispatch. Keys ``'start'``
            and ``'end'`` must be strings in ``HH:MM:SS`` format.
        performance_incentive (dict): Incentive revenue expressed as a
            ``{units, val}`` dict. ``units`` must be one of ``'$/kWh'``,
            ``'$/MWh'``, or ``'$/Wh'``; ``val`` is the numeric amount.
            Example: ``{units: '$/kWh', val: 14.0}``.
        charge_efficiency (float): Charge efficiency in [0, 1].
            Defaults to 1.0.
        discharge_efficiency (float): Discharge efficiency in [0, 1].
            Defaults to 1.0.
        n_max_events (int): Maximum discharge events per calendar month.
            Defaults to 10.
        n_control_window (float): Rolling window size in **hours**.
            Converted to an integer timestep count during ``setup()``
            using the simulation ``dt``, so the same value works at any
            resolution. Example: ``10`` at a 30-min ``dt`` gives a
            20-timestep window. Defaults to ``24``.
        signal_threshold_percentile (float): Percentile (0-100) used to
            compute the signal threshold for each rolling window. Only
            timesteps at or above this percentile of the window signal are
            eligible for dispatch. Defaults to 0.0 (all timesteps eligible).
        event_duration (dict): Total dispatch-event duration
            expressed as a ``{units, val}`` dict, where ``units`` is any
            pandas timedelta unit string (e.g. ``'h'``, ``'min'``,
            ``'s'``) and ``val`` is the numeric amount. When set, the
            eligible dispatch window is computed dynamically per calendar
            day: the peak-signal timestep within ``peak_window`` is
            located and every timestep within ``event_duration / 2`` of
            that peak is marked eligible (the window may extend
            beyond the static ``peak_window`` boundaries). When ``None``
            (default) the static ``peak_window`` mask is used unchanged.
            Example: ``{units: 'h', val: 4}`` is +/- 2 h around the daily peak.
    """

    max_charge_rate: float = field()
    supervisory_signal: list = field()
    peak_window: dict = field()
    performance_incentive: dict = field(validator=has_required_keys(["units", "val"]))
    charge_efficiency: float = field(validator=range_val(0, 1), default=1.0)
    discharge_efficiency: float = field(validator=range_val(0, 1), default=1.0)
    n_max_events: int = field(default=10)
    n_control_window: float = field(default=24.0)
    signal_threshold_percentile: float = field(default=0.0, validator=range_val(0, 100))
    event_duration: dict = field(default=None)
    min_peak_separation: dict = field(default=None)

    _INCENTIVE_TO_KWH: ClassVar[dict] = {"$/kWh": 1.0, "$/MWh": 1e-3, "$/Wh": 1e3}

    def __attrs_post_init__(self):
        # Make sure n_control_window is an int
        self.n_control_window = int(round(self.n_control_window))
        super().__attrs_post_init__()

        for key in ("units", "val"):
            if key not in self.performance_incentive:
                raise ValueError(
                    f"performance_incentive is missing required key '{key}'. "
                    "Expected dict with 'units' (e.g. '$/kWh') and 'val' (numeric)."
                )
        if self.performance_incentive["units"] not in self._INCENTIVE_TO_KWH:
            raise ValueError(
                f"performance_incentive 'units' must be one of "
                f"{list(self._INCENTIVE_TO_KWH)}, got {self.performance_incentive['units']}."
            )
        if not isinstance(self.performance_incentive.get("val"), int | float):
            raise ValueError(
                "performance_incentive 'val' must be a numeric value "
                f"(int or float), got {type(self.performance_incentive.get('val')).__name__}."
            )

        for field_name, value in (
            ("event_duration", self.event_duration),
            ("min_peak_separation", self.min_peak_separation),
        ):
            if value is not None:
                for key in ("units", "val"):
                    if key not in value:
                        raise ValueError(
                            f"{field_name} is missing required key '{key}'. "
                            "Expected dict with 'units' (pandas timedelta unit string) "
                            "and 'val' (int or float)."
                        )
                if not isinstance(value["val"], int | float):
                    raise ValueError(
                        f"{field_name} 'val' must be a numeric value "
                        f"(int or float), got {type(value['val']).__name__}."
                    )


class PeakLoadManagementOptimizedStorageController(PyomoStorageControllerBaseClass):
    """Demand-response storage controller using a rolling-horizon MILP.

    Each call to the dispatch solver iterates over the full simulation in
    windows of length ``n_control_window``. For each window it receives
    the monthly LMP forecast, solves the MILP to maximize incentive
    revenue, then passes the resulting dispatch commands to the
    performance model. The terminal SOC of each window is carried forward
    as the initial SOC of the next window.
    """

    dr_model: Any
    problem_state: DispatchProblemState

    def setup(self):
        """Initialize config, register OpenMDAO inputs, and pre-compute static masks.

        Raises:
            ValueError: If the length of the time series built from
                ``plant_config`` does not match ``n_timesteps``.
        """
        self.config = PeakLoadManagementOptimizedControllerConfig.from_dict(
            merge_shared_inputs(self.options["tech_config"]["model_inputs"], "control")
        )

        self.add_input(
            "max_charge_rate",
            val=self.config.max_charge_rate,
            units=self.config.commodity_rate_units,
            desc="Maximum charge/discharge rate P_max",
        )
        self.add_input(
            "storage_capacity",
            val=self.config.max_capacity,
            units=f"{self.config.commodity_rate_units}*h",
            desc="Total storage capacity",
        )

        sim = self.options["plant_config"]["plant"]["simulation"]
        self.n_timesteps = int(sim["n_timesteps"])
        self.dt_seconds = int(sim["dt"])

        # n_control_window is stored in hours; convert to timesteps now that dt is known.
        n_cw_steps = max(1, int(round(self.config.n_control_window * 3600 / self.dt_seconds)))
        object.__setattr__(self.config, "n_control_window", n_cw_steps)
        scil = self.config.system_commodity_interface_limit
        scil_list: list[float] = list(scil) if isinstance(scil, list | tuple) else [float(scil)]
        if len(scil_list) != n_cw_steps:
            object.__setattr__(
                self.config, "system_commodity_interface_limit", [scil_list[0]] * n_cw_steps
            )

        super().setup()

        self.updated_initial_soc = self.config.init_soc_fraction

        self.commodity_info = {
            "commodity_name": self.config.commodity,
            "commodity_storage_units": self.config.commodity_rate_units,
        }

        self.time_index = build_time_series_from_plant_config(
            self.options["plant_config"]
        )  # DatetimeIndex of length n_timesteps

        if len(self.time_index) != self.n_timesteps:
            raise ValueError(
                f"Time series length {len(self.time_index)} != n_timesteps {self.n_timesteps}"
            )

        self.in_peak_window = self._compute_peak_window_mask()  # bool array, shape (T,)
        self.month_ids = self._compute_month_ids()  # int array,  shape (T,)

    def _parse_peak_window(self) -> tuple:
        """Parse the ``peak_window`` config entry into ``datetime.time`` objects.

        Returns:
            tuple[datetime.time, datetime.time]: ``(start, end)`` times.

        Raises:
            ValueError: If ``'start'`` or ``'end'`` keys are missing, or
                if either value is not a string in ``HH:MM:SS`` format.
        """
        pw = dict(self.config.peak_window)
        if "start" not in pw or "end" not in pw:
            raise ValueError("peak_window must contain 'start' and 'end' keys")
        for key in ("start", "end"):
            val = pw[key]
            if not isinstance(val, str) or len(val.split(":")) != 3:
                raise ValueError(
                    f"peak_window {key} value must be a string in HH:MM:SS format, got {val}."
                )
            pw[key] = datetime.strptime(val, "%H:%M:%S").time()
        return pw["start"], pw["end"]

    def _compute_peak_window_mask(self) -> np.ndarray:
        """Build a boolean mask that is ``True`` for timesteps inside the peak window.

        Returns:
            np.ndarray: Boolean array of shape ``(n_timesteps,)``.

        Raises:
            ValueError: If ``peak_window`` end time is before start time.
        """
        start, end = self._parse_peak_window()
        times = pd.DatetimeIndex(self.time_index).time
        if end < start:
            raise ValueError("peak_window end time must be after start time.")
        return np.array([start <= t <= end for t in times])

    def _compute_month_ids(self) -> np.ndarray:
        """Return the calendar month index (1-12) for each timestep.

        Returns:
            np.ndarray: Integer array of shape ``(n_timesteps,)``.
        """
        return pd.DatetimeIndex(self.time_index).month.to_numpy()

    def _compute_eligible_mask(
        self,
        signal_window: np.ndarray,
        dispatch_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """Build a boolean mask for timesteps whose signal meets the dispatch threshold.

        The threshold percentile is computed from ``signal_window`` values
        that fall inside ``dispatch_mask`` (i.e. the current dispatch
        window).
        When ``dispatch_mask`` is ``None`` the full ``signal_window`` is
        used. When ``signal_threshold_percentile`` is 0.0 all timesteps
        are eligible.

        Args:
            signal_window (np.ndarray): Signal values for the current
                rolling window.
            dispatch_mask (np.ndarray | None): Boolean mask of shape
                ``(len(signal_window),)`` indicating which timesteps
                belong to the current dispatch window. Defaults to
                ``None`` (use full window).

        Returns:
            np.ndarray: Boolean array of shape ``(len(signal_window),)``.
                ``True`` where ``signal_t >= threshold``.
        """
        mask = (
            dispatch_mask if dispatch_mask is not None else np.ones(len(signal_window), dtype=bool)
        )

        if self.config.signal_threshold_percentile == 0.0 or not mask.any():
            return mask.copy()

        threshold = np.percentile(signal_window[mask], self.config.signal_threshold_percentile)
        eligible = mask & (signal_window >= threshold)

        if self.config.min_peak_separation is not None and eligible.any():
            sep_steps = (
                pd.Timedelta(
                    value=self.config.min_peak_separation["val"],
                    unit=self.config.min_peak_separation["units"],
                ).total_seconds()
                / self.dt_seconds
            )
            # Greedily keep peaks from highest to lowest signal, dropping any peak
            # that falls within sep_steps of an already-kept peak.
            peak_indices = np.where(eligible)[0]
            order = np.argsort(-signal_window[peak_indices])
            kept: list[int] = []
            for idx in peak_indices[order]:
                if all(abs(int(idx) - k) >= sep_steps for k in kept):
                    kept.append(int(idx))
            eligible = np.zeros(len(signal_window), dtype=bool)
            eligible[kept] = True

        return eligible

    def _compute_event_window_mask(
        self,
        eligible_mask: np.ndarray,
        window_start: int,
    ) -> np.ndarray:
        """Expand each eligible peak timestep by ±event_duration/2.

        For each calendar day, every True timestep in ``eligible_mask`` is
        treated as a peak and all timesteps within ``event_duration / 2``
        of it are marked eligible. When ``event_duration`` is ``None``
        returns ``eligible_mask`` unchanged.

        Args:
            eligible_mask (np.ndarray): Boolean mask of peak timesteps,
                shape ``(window_len,)``.
            window_start (int): Index of the first timestep of this
                window into ``self.time_index``.

        Returns:
            np.ndarray: Boolean mask of shape ``(window_len,)``.
        """
        if self.config.event_duration is None:
            return eligible_mask.copy()

        half_steps = (
            pd.Timedelta(
                value=self.config.event_duration["val"],
                unit=self.config.event_duration["units"],
            ).total_seconds()
            / 2.0
            / self.dt_seconds
        )
        indices = np.arange(len(eligible_mask))
        peak_indices = np.where(eligible_mask)[0]
        distances = np.abs(indices[:, None] - peak_indices[None, :])
        return (distances <= half_steps).any(axis=1)

    def pyomo_setup(self, discrete_inputs):
        """Return the rolling-horizon dispatch solver callable.

        Args:
            discrete_inputs (dict): OpenMDAO discrete inputs.

        Returns:
            callable: ``pyomo_dispatch_solver(performance_model,
            performance_model_kwargs, inputs)`` that iterates over the
            simulation in windows of ``n_control_window`` timesteps.
            For each window it:

            1. Builds a fresh MILP from the window's signal slice.
            2. Solves the MILP with GLPK.
            3. Calls ``performance_model`` with the resulting dispatch
               commands.
            4. Carries the terminal SOC into the next window.

            Returns ``(storage_out, soc_out)`` - two ``np.ndarray`` of
            length ``n_timesteps``.
        """

        def pyomo_dispatch_solver(
            performance_model,
            performance_model_kwargs,
            inputs,
            commodity_name=self.config.commodity,
        ):
            storage_out = np.zeros(self.n_timesteps)
            soc_out = np.zeros(self.n_timesteps)

            # Track events used per calendar month so the monthly cap is
            # respected across window boundaries.
            events_used_per_month = {}

            n_w: int = int(self.config.n_control_window)
            window_start_indices = list(range(0, self.n_timesteps, n_w))

            for window_start in window_start_indices:
                window_len: int = min(n_w, self.n_timesteps - window_start)

                n_windows = len(window_start_indices)
                report_every = max(1, n_windows // 4)
                window_idx = window_start // n_w
                if window_idx % report_every == 0:
                    round(window_start / self.n_timesteps * 100)

                month_ids_w = self.month_ids[window_start : window_start + window_len]
                remaining_budget = {
                    int(m): max(
                        0,
                        self.config.n_max_events - events_used_per_month.get(int(m), 0),
                    )
                    for m in np.unique(month_ids_w)
                }

                self.dr_model = self._build_dr_model(
                    window_start=window_start,
                    window_len=window_len,
                    init_soc=self.updated_initial_soc,
                    remaining_budget=remaining_budget,
                )
                self.problem_state = DispatchProblemState()

                self.solve_dispatch_model(
                    start_time=window_start,
                    n_days=self.n_timesteps // 24,
                )

                for t in range(window_len):
                    if pyomo.value(self.dr_model.discharge[t]) > 0.5:
                        m = int(month_ids_w[t])
                        events_used_per_month[m] = events_used_per_month.get(m, 0) + 1

                storage_out_window, soc_window = performance_model(
                    self.storage_dispatch_commands,
                    **performance_model_kwargs,
                    sim_start_index=window_start,
                )

                # Performance model returns SOC in percent.
                self.updated_initial_soc = soc_window[-1] / 100.0

                for j in range(window_len):
                    storage_out[window_start + j] = storage_out_window[j]
                    soc_out[window_start + j] = soc_window[j]

            return storage_out, soc_out

        return pyomo_dispatch_solver

    def initialize_parameters(self, inputs):
        """Sync OpenMDAO inputs into the config.

        Args:
            inputs (dict): OpenMDAO inputs dict. Recognised keys are
                ``'max_charge_rate'`` and ``'storage_capacity'``.
        """
        if "max_charge_rate" in inputs:
            object.__setattr__(self.config, "max_charge_rate", float(inputs["max_charge_rate"][0]))
        if "storage_capacity" in inputs:
            object.__setattr__(self.config, "max_capacity", float(inputs["storage_capacity"][0]))

    def _build_dr_model(
        self,
        window_start: int,
        window_len: int,
        init_soc: float,
        remaining_budget: dict,
    ) -> pyomo.ConcreteModel:
        """Build the DR MILP for a single rolling window.

        Args:
            window_start (int): Timestep index of the first hour
                in this window.
            window_len (int): Number of timesteps in this window
            init_soc (float): State-of-charge fraction at the start of
                this window.
            remaining_budget (dict): Mapping of ``month_id (int)`` to
                remaining event slots for that month. Computed by
                subtracting events already dispatched in earlier windows
                from ``n_max_events``, so the monthly cap is respected
                across windows.

        Returns:
            pyomo.ConcreteModel: Fully formed MILP ready to solve.
        """
        m: Any = pyomo.ConcreteModel(name="plm_dr")

        P_max = self.config.max_charge_rate
        E_max = self.config.max_capacity * (
            self.config.max_soc_fraction - self.config.min_soc_fraction
        )
        eta_c = self.config.charge_efficiency
        eta_d = self.config.discharge_efficiency
        soc_max = self.config.max_soc_fraction
        soc_min = self.config.min_soc_fraction
        incentive = (
            self.config.performance_incentive["val"]
            * self.config._INCENTIVE_TO_KWH[self.config.performance_incentive["units"].strip()]
        )
        N_max = self.config.n_max_events

        w = slice(window_start, window_start + window_len)
        in_peak_window_w = self.in_peak_window[w]
        month_ids_w = self.month_ids[w]
        signal_w = np.asarray(self.config.supervisory_signal, dtype=float)[w]
        eligible_t_w = self._compute_eligible_mask(signal_w, in_peak_window_w)
        dispatch_window_w = self._compute_event_window_mask(eligible_t_w, window_start)
        eligible_t_w = dispatch_window_w

        months_in_window = np.unique(month_ids_w).tolist()

        m.T = pyomo.Set(initialize=range(window_len), doc="Timesteps in window")
        m.M = pyomo.Set(initialize=months_in_window, doc="Months in window")

        m.discharge = pyomo.Var(
            m.T, domain=pyomo.Binary, doc="Discharge binary: 1 = discharging at timestep t"
        )
        m.charge = pyomo.Var(
            m.T, domain=pyomo.Binary, doc="Charge binary: 1 = charging at timestep t"
        )
        m.soc = pyomo.Var(
            m.T,
            domain=pyomo.NonNegativeReals,
            bounds=(soc_min, soc_max),
            doc="State of charge SoC_t",
        )

        dt_hours = self.dt_seconds / 3600.0
        m.objective = pyomo.Objective(
            expr=-incentive * P_max * dt_hours * sum(m.discharge[t] for t in m.T),
            sense=pyomo.minimize,
        )

        m.peak_window_only = pyomo.Constraint(
            m.T,
            rule=lambda mdl, t: (
                mdl.discharge[t] == 0 if not dispatch_window_w[t] else pyomo.Constraint.Skip
            ),
        )

        m.high_signal_only = pyomo.Constraint(
            m.T,
            rule=lambda mdl, t: mdl.discharge[t] <= int(eligible_t_w[t]),
        )

        def max_events_rule(mdl, month):
            ts_in_month = [t for t in mdl.T if month_ids_w[t] == month]
            if not ts_in_month:
                return pyomo.Constraint.Skip
            budget = remaining_budget.get(month, N_max)
            return sum(mdl.discharge[t] for t in ts_in_month) <= budget

        m.max_events = pyomo.Constraint(m.M, rule=max_events_rule)

        m.soc_init = pyomo.Constraint(expr=m.soc[0] == init_soc)

        def soc_evolution_rule(mdl, t):
            if t == 0:
                return pyomo.Constraint.Skip
            return mdl.soc[t] == (
                mdl.soc[t - 1]
                + eta_c * mdl.charge[t] * P_max * dt_hours / E_max
                - mdl.discharge[t] * P_max * dt_hours / (eta_d * E_max)
            )

        m.soc_evolution = pyomo.Constraint(m.T, rule=soc_evolution_rule)

        m.no_simultaneous = pyomo.Constraint(
            m.T,
            rule=lambda mdl, t: mdl.discharge[t] + mdl.charge[t] <= 1,
        )

        m.no_charge_in_window = pyomo.Constraint(
            m.T,
            rule=lambda mdl, t: (
                mdl.charge[t] == 0 if dispatch_window_w[t] else pyomo.Constraint.Skip
            ),
        )

        return m

    def solve_dispatch_model(self, start_time: int = 0, n_days: int = 0):
        """Solve the DR MILP for the current window and record solver metrics.

        Args:
            start_time (int): Timestep index of the window start.
                Used only for error messages and metrics. Defaults to 0.
            n_days (int): Total simulation days. Passed to
                ``DispatchProblemState.store_problem_metrics``.
                Defaults to 0.

        Raises:
            RuntimeError: If GLPK returns a not OK status or an
                unacceptable termination condition.
        """
        from pyomo.opt import SolverStatus, TerminationCondition

        solver_results = self.glpk_solve_call(self.dr_model)

        status = solver_results.solver.status
        tc = solver_results.solver.termination_condition
        acceptable = (
            TerminationCondition.optimal,
            TerminationCondition.feasible,
            TerminationCondition.maxTimeLimit,
        )
        if status != SolverStatus.ok or tc not in acceptable:
            raise RuntimeError(
                f"PLM MILP solver failed at window start={start_time}: "
                f"status={status}, termination={tc}. "
                f"init_soc={self.updated_initial_soc:.4f}, "
                f"window_len={len(list(self.dr_model.T))}"
            )

        self.problem_state.store_problem_metrics(
            solver_results,
            start_time,
            n_days,
            pyomo.value(self.dr_model.objective),
        )

    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        """Build the DR dispatch solver and write it to discrete outputs.

        Args:
            inputs (dict): OpenMDAO continuous inputs.
            outputs (dict): OpenMDAO continuous outputs.
            discrete_inputs (dict): OpenMDAO discrete inputs.
            discrete_outputs (dict): OpenMDAO discrete outputs. The key
                ``'pyomo_dispatch_solver'`` is set to the callable
                returned by :meth:`pyomo_setup`.
        """
        discrete_outputs["pyomo_dispatch_solver"] = self.pyomo_setup(discrete_inputs)

    @staticmethod
    def glpk_solve_call(
        pyomo_model: pyomo.ConcreteModel,
        log_name: str = "",
        user_solver_options: dict | None = None,
    ):
        """Solve a Pyomo MILP with GLPK.

        Args:
            pyomo_model (pyomo.ConcreteModel): The model to solve.
            log_name (str): Optional log file name passed to
                ``SolverOptions``. Defaults to ``''``.
            user_solver_options (dict | None): Optional overrides for
                GLPK solver options. Defaults to ``None``.

        Returns:
            pyomo.opt.SolverResults: Raw results object from GLPK.
        """
        glpk_solver_options = {"cuts": None, "presol": None, "tmlim": 300}
        solver_options = SolverOptions(glpk_solver_options, log_name, user_solver_options, "log")
        with pyomo.SolverFactory("glpk") as solver:
            results = solver.solve(pyomo_model, options=solver_options.constructed, tee=False)
        return results

    @property
    def storage_dispatch_commands(self) -> list:
        """Net dispatch commands for the solved window.

        Returns:
            list[float]: ``(u_t - v_t) * P_max`` for each timestep in
            the solved window. Positive = discharge, negative = charge.
        """
        P_max = self.config.max_charge_rate
        return [
            (pyomo.value(self.dr_model.discharge[t]) - pyomo.value(self.dr_model.charge[t])) * P_max
            for t in self.dr_model.T
        ]
