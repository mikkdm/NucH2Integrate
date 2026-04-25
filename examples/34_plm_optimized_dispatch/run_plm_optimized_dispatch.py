"""
This example demonstrates demand-response storage dispatch using a rolling-horizon
MILP controller. The battery is scheduled to discharge during high-LMP peak hours
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from h2integrate.core.h2integrate_model import H2IntegrateModel

EXAMPLE_DIR = Path(__file__).parent

model = H2IntegrateModel(EXAMPLE_DIR / "34_plm_optimized_dispatch.yaml")
model.setup()

battery_config = model.technology_config["technologies"]["battery"]["model_inputs"]
sim = model.plant_config["plant"]["simulation"]

N = int(sim["n_timesteps"])
percentile = battery_config["control_parameters"]["signal_threshold_percentile"]

model.run()

lmp = np.array(battery_config["control_parameters"]["supervisory_signal"])[:N]
battery_power = model.prob.get_val("battery.storage_electricity_discharge", units="kW")
battery_charge = model.prob.get_val("battery.storage_electricity_charge", units="kW")
soc_pct = model.prob.get_val("battery.SOC", units="percent")
unmet_demand = model.prob.get_val("grid_buy.electricity_unmet_demand", units="kW")

capex = model.prob.get_val("battery.CapEx", units="USD")[0]
opex = model.prob.get_val("battery.OpEx", units="USD/year")[0]
n_discharge_events = int((battery_power > 0).sum())
total_energy_discharged_kwh = battery_power.sum()           # kW * 1 hr per timestep
total_energy_charged_kwh = abs(battery_charge.sum())
incentive_revenue = (
    n_discharge_events
    * battery_config["shared_parameters"]["max_charge_rate"]
    * battery_config["control_parameters"]["performance_incentive"]
)

print(f"\n--- Results Summary ---")
print(f"Battery CapEx:              ${capex:>12,.0f}")
print(f"Battery OpEx:              ${opex:>12,.0f} /year")
print(f"Discharge events (annual): {n_discharge_events:>12}")
print(f"Total energy discharged:   {total_energy_discharged_kwh:>12,.1f} kWh")
print(f"Total energy charged:      {total_energy_charged_kwh:>12,.1f} kWh")
print(f"Estimated incentive rev:   ${incentive_revenue:>12,.0f} /year")
print(f"Total unmet demand:        {unmet_demand.sum():>12,.1f} kWh")

time_index = pd.date_range(
    start=pd.Timestamp(sim["start_time"], tz=int(sim["timezone"])),
    periods=N,
    freq=pd.to_timedelta(int(sim["dt"]), unit="s"),
)

W = 14 * 24  # plot first 14 days
ti = time_index[:W]
days = pd.date_range(ti[0].normalize(), periods=14, freq="D", tz=ti.tz)


def shade_peaks(ax):
    for day in days:
        ax.axvspan(
            day + pd.Timedelta(hours=14),
            day + pd.Timedelta(hours=18),
            color="orange", alpha=0.10, linewidth=0, zorder=0,
        )


plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 7))

ax = axes[0]
shade_peaks(ax)
ax.plot(ti, lmp[:W], color="steelblue", linewidth=1.0)
ax.axhline(np.percentile(lmp, percentile), color="k", linestyle="--", linewidth=0.8)
ax.plot(ti[battery_power[:W] > 0], lmp[:W][battery_power[:W] > 0], "r*", markersize=8, zorder=5)
ax.set_ylabel("LMP ($/MWh)", fontsize=8)
ax.set_ylim(bottom=0)

ax = axes[1]
shade_peaks(ax)
ax.plot(ti, soc_pct[:W], color="g", linewidth=1.0)
ax.axhline(90, color="gray", linestyle=":", linewidth=0.7)
ax.axhline(10, color="gray", linestyle=":", linewidth=0.7)
ax.set_ylabel("SOC (%)", fontsize=8)
ax.set_ylim([0, 105])

plt.tight_layout()
plt.savefig("plm_optimized_dispatch.png", dpi=150, bbox_inches="tight")
