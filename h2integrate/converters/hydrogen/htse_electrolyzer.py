import math

import numpy as np
from attrs import field, define

from h2integrate.core.utilities import merge_shared_inputs
from h2integrate.core.validators import gt_zero, contains
from h2integrate.core.model_baseclasses import ResizeablePerformanceModelBaseConfig
from h2integrate.converters.hydrogen.utilities import size_electrolyzer_for_hydrogen_demand
from h2integrate.converters.hydrogen.electrolyzer_baseclass import ElectrolyzerPerformanceBaseClass


@define(kw_only=True)
class HTSElectrolyzerPerformanceModelConfig(ResizeablePerformanceModelBaseConfig):
    """
    Configuration class for the HTSElectrolyzerPerformanceModel.

    Args:
        Currently a vast simplification when compared against the PEM system, but further additions to be anticipated. The total size of the system is nominally n_clusters*cluster_rating_MW. 
        cluster_rating_MW
    """

    n_clusters: int = field(validator=gt_zero)
    nominal_heat_required: float = field()
    nominal_electricity_required: float = field()
    location: str = field(validator=contains(["onshore", "offshore"]))
    cluster_rating_MW: float = field(validator=gt_zero)
    eol_eff_percent_loss: float = field(validator=gt_zero)
    uptime_hours_until_eol: int = field(validator=gt_zero)
    include_degradation_penalty: bool = field()
    turndown_ratio: float = field(validator=gt_zero)
    electrolyzer_capex: int = field()


class HTSElectrolyzerPerformanceModel(ElectrolyzerPerformanceBaseClass):
    """
    An OpenMDAO component that wraps the HTS-electrolyzer model.
    Takes electricity and heat input and outputs hydrogen and oxygen generation rates.
    """

    def setup(self):
        self.config = HTSElectrolyzerPerformanceModelConfig.from_dict(
            merge_shared_inputs(self.options["tech_config"]["model_inputs"], "performance"),
            strict=False,
            additional_cls_name=self.__class__.__name__,
        )
        super().setup()
        self.add_output(
            "efficiency",
            val=0.0,
            units="unitless",
            desc="Average efficiency of the electrolyzer",
        )

        self.add_output(
            "time_until_replacement", val=80000.0, units="h", desc="Time until replacement"
        )

        self.add_input(
            "n_clusters",
            val=self.config.n_clusters,
            units="unitless",
            desc="number of electrolyzer clusters in the system",
        )
        self.add_input("heat_in", val = 0.0, shape = n_timesteps, units = "MW", desc = "Heat input")
        self.add_input("nom_heat", val = self.config.nominal_heat_required, units = "kW*h/kg", desc = "Specific heat required")
        self.add_input("nom_elec", val = self.config.nominal_electricity_required, units = "kW*h/kg", desc = "Specific electricity required")
        self.add_output(
            "electrolyzer_size_mw",
            val=0.0,
            units="MW",
            desc="Size of the electrolyzer in MW",
        )
        self.add_input("cluster_size", val=-1.0, units="MW")
        self.add_input("max_hydrogen_capacity", val=1000.0, units="kg/h")
        self.add_input("water_sourced", val = 0.0, shape = n_timesteps, units = "kg/hr", desc = "Water input")



        self.add_output("hydrogen_out", val = 0.0, shape = n_timesteps, units = "kg/hr", desc = "Hydrogen produced")
        # TODO: add feedstock inputs and consumption outputs

    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        plant_life = self.options["plant_config"]["plant"]["plant_life"]



        electrolyzer_size_kw = inputs["n_clusters"][0] * self.config.cluster_rating_MW * 1000
        #Calculates the amount of heat required
        nominal_heat_dem = electrolyzer_size_kw * inputs["nom_heat"] / inputs["nom_elec"]
        #Reducing the amount of heat used by the capacity of the system
        actual_heat_internal = min(nominal_heat_dem, inputs["heat_in"][:])
        
        #restrict possible hydrogen production by water sourcing - or should this be an output? 
        #could have output["water_demand"] = "hydrogen_out"*18.015/2.016 ? 

        max_hydrogen_produced = inputs["water_sourced"][:] * 2.016 / 18.015

        #calculate maximum heat, assume that if heat is not there, it's replaced by electricity
        driver_hydrogen_produced = np.mininum(max_hydrogen_produced, inputs["hydrogen_demand"][:])
        heat_demand = driver_hydrogen_produced / nom_heat
        heat_to_electrolyzer_kw = inputs["heat_in"][:]
        actual_heat = np.minimum(heat_to_electrolyzer_kw, heat_demand)
        electrical_demand_adjusted = driver_hydrogen_produced / nom_elec + (heat_demand - heat_to_electrolyzer)
        elec_to_electrolyzer_kw = inputs["electricity_in"][:]
        actual_elec = np.minimum(elec_to_electrolyzer_kw, electrical_demand_adjusted)
        total_energy_required = inputs["nom_heat"] + inputs["nom_elec"]
        outputs["hydrogen_out"] = (actual_elec + actual_heat) / total_energy_required
        hydrogen_production_capacity_required_kgphr = []



class HTSECostModel(CostModelBaseClass):
    def setup(self)
    def compute(self, inputs, outputs)
