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
    This class does not go into stack-specific operations, so this is more of a bulk model but could be extended later to account for stack physics.
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
    pressure_H2: float = field(validator=gt_zero)


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
        

        self.add_output("water_demand", val = 0.0, shape = n_timesteps, units = "kg/hr", desc = "Water demand")
        self.add_output("heat_demand", val = 0.0, shape = n_timesteps, units = "kW", desc = "Heat demand")
        self.add_output("electricity_demand", val = 0.0, shape = n_timesteps, units = "kW", desc = "Electricity demand")

        self.add_output("hydrogen_out", val = 0.0, shape = n_timesteps, units = "kg/hr", desc = "Hydrogen produced")
        
        # TODO: add feedstock inputs and consumption outputs

    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
       """ 
            Assume that there are separations between actual heat and electricity in and the hydrogen demand"
       """
        plant_life = self.options["plant_config"]["plant"]["plant_life"]

        electrolyzer_size_kw = inputs["n_clusters"][0] * self.config.cluster_rating_MW * 1000
        #Calculates the amount of heat required
        nominal_heat_dem = inputs["hydrogen_demand"][:] * inputs["nom_heat"]
        outputs["heat_demand"] = nominal_heat_dem


        #Reducing the amount of heat used by the capacity of the system
        actual_heat_internal = min(nominal_heat_dem, inputs["heat_in"][:])        
        outputs["water_demand"] = inputs["hydrogen_demand][:] * 18.015 / 2.016
      
        #calculate maximum heat, assume that if heat is not there, it's replaced by electricity
  

        electrical_demand_adjusted = driver_hydrogen_produced / nom_elec + (nominal_heat_dem - actual_heat_internal)
        elec_to_electrolyzer_kw = inputs["electricity_in"][:]
        actual_elec = np.minimum(elec_to_electrolyzer_kw, electrical_demand_adjusted)
        total_energy_required = inputs["nom_heat"] + inputs["nom_elec"]
        outputs["hydrogen_out"] = (actual_elec + actual_heat) / total_energy_required
    

class HTSECostModelConfig(CostModelBaseConfig):
    """Configuration class for an HTSE cost model

    Attributes: 
        
    """

    capex_USD_per_kW: float = field(validator=gte_zero)
    fixed_USD_per_kW_per_year: float = field(validator= gte_zero)

class HTSECostModel(CostModelBaseClass):
    def setup(self)
        self.config = HTSECostModelConfig.from_dict(
            merge_shared_inputs(self.options["tech_config"]["model_inputs"], "cost"),
            additional_cls_name=self._class_._name_,)


        super().setup()

        self.add_input("electrolyzer_size_mw", val=0, units="kW", desc="Size of the electrolyzer in kW")

        self.add_input("unit_capex", val=self.config.capex_USD_per_kW, units="USD/kW", desc = "CapEx of electrolyzer in USD/kW",)

        self.add_input("fixed_opex", val=self.config.fixed_USD_per_kW_per_year, units="USD/kw/yr", desc="Fixed OpEx of electrolyzer in USD/(kW-year)",)

    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs)
        outputs["CapEx"] = inputs["unit_capex"] * electrolyzer_size_kw
        outputs["OpEx"] = inputs["fixed_capex"] * electrolyzer_size_kw
        




