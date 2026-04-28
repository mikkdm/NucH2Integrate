import openmdao.api as om
from attrs import field, define

from h2integrate.core.utilities import BaseConfig, merge_shared_inputs
from h2integrate.core.model_baseclasses import CostModelBaseClass

@define
class SimpleThermalNuclearReactorConfig(BaseConfig):
    hourly_power_production: float = field()
    high_pressure_electrical_efficiency: float = field()
    low_pressure_electrical_efficiency: float = field()
    nuclear_reactor_capacity: float = field()

class SimpleThermalNuclearReactorPerformanceModel(om.ExplicitComponent):
    """
    Simple nuclear reactor performance model.
    """

    def initialize(self):
        self.options.declare("tech_config", types=dict)
        self.options.declare("plant_config", types=dict)
        self.options.declare("driver_config", types=dict)

    def setup(self):
        n_timesteps = self.options["plant_config"]["plant"]["simulation"]["n_timesteps"]
        self.config = SimpleNuclearReactorConfig.from_dict(
            self.options["tech_config"]["model_inputs"]["performance_parameters"],
            strict=False,
        )
        #external input
        self.add_input("hourly_power_production", val=0.0, shape=self.n_timesteps, units="MW")
        self.add_input("external_heat_demand", val=0.0, shape=self.n_timesteps, units="MW")
        self.add_input("nuclear_reactor_capacity", val=self.config.nuclear_reactor_capacity, units="MW")

       
        # Efficiencies Inputs 
        self.add_input("high_pressure_electrical_efficiency", val=self.config.high_pressure_electrical_efficiency, units=None) 
        self.add_input("low_pressure_electrical_efficiency", val=self.config.low_pressure_electrical_efficiency, units=None) 

        # Ratios Inputs - now unused

        # Outputs
        self.add_output("high_pressure_heat_demanded", val=0, shape=self.n_timesteps, units="kW")
        self.add_output("high_pressure_heat", val=0, shape=self.n_timesteps, units="kW")
        self.add_output("low_pressure_heat", val=0, shape=self.n_timesteps, units="kW")
        self.add_output("heat_dispatched", val=0, shape=self.n_timesteps, units="kW")
        self.add_output("electricity_out", val=0, shape=self.n_timesteps, units="kW")

        

    def compute(self, inputs, outputs):
        # Valid options for this are 'heat' and 'electricity'. 
        operating_mode = discrete_inputs["operating_mode"]
        
        outputs["high_pressure_heat_demanded"] = (inputs["hourly_power_production"][:] + inputs["external_heat_demand"]
            [:]*inputs["low_pressure_electrical_efficiency"]) / (inputs["high_pressure_electrical_efficiency"] +
            (1-inputs["high_pressure_electrical_efficiency"]) * inputs["low_pressure_electrical_efficiency"])
        #this equation calculates the necessary reactor heat generated to meet both the heat and electricity demands
        
        outputs["high_pressure_heat"] = np.minimum(outputs["high_pressure_heat_demanded"], (inputs["hourly_power_production"][:] / 
            (inputs["high_pressure_electrical_efficiency"] + (1-inputs["high_pressure_electrical_efficiency"]) 
            * inputs["low_pressure_electrical_efficiency"])))
     	#heat dispatch centric  
        if operating_mode == "heat" 
            outputs["heat_dispatched"] = np.minimum(inputs["external_heat_demand"][:], 
                outputs["high_pressure_heat"] * (1 - inputs["high_pressure_electrical_efficiency"])

            outputs["low_pressure_heat"] = (1 - inputs"high_pressure_electrical_efficiency"]) * (outputs["high_pressure_heat"] -
            outputs["heat_dispatched"])
            outputs["electricity_out"] = (inputs["high_pressure_electrical_efficiency"]*outputs["high_pressure_heat"] +
                inputs["low_pressure_electrical_efficiency"]*outputs["low_pressure_heat"])
        
        #electricity dispatch centric
        elif operating_mode == "electricity"
            outputs["low_pressure_heat"] = np.minimum((inputs["hourly_power_production"][:] - 
                outputs["high_pressure_heat"]*inputs["high_pressure_electrical_efficiency"]) / 
                inputs["low_pressure_electrical_efficiency"] , 
                outputs["high_pressure_heat"][:] * (1 - inputs["high_pressure_electrical_efficiency"])
                )
            outputs["heat_dispatched"] = (outputs["high_pressure_heat"] * (1 - #inputs["high_pressure_electrical_efficiency"]) 
                - outputs["low_pressure_heat"])
            outputs["electricity_out"] = (inputs["high_pressure_electrical_efficiency"] * 
                outputs["high_pressure_heat"] + inputs["low_pressure_electrical_efficiency"] * outputs["low_pressure_heat"])
        else:
            NotImplementedError((f"The nuclear model should be either heat or electricity, now values are simply passed through"))
            outputs["heat_dispatched"] = inputs["external_heat_demand"]
            outputs["electricity_out"] = inputs["hourly_power_production"]
            outputs["low_pressure_heat"] = outputs["high_pressure_heat"] - outputs["electricity_out"] - outputs["heat_dispatched"]



@define
class SimpleThermalNuclearReactorCostConfig(BaseConfig):
    nuclear_reactor_rated_capacity: float = field()
    nuclear_reactor_upfront_cost: float = field()    
    nuclear_reactor_fixed_om_cost: float = field()
    nuclear_reactor_variable_om_cost: float = field()
    cost_year: float = field(default = 2025)
class SimpleThermalNuclearReactorCostModel(CostModelBaseClass):
    """
    Simple nuclear reactor cost model.
    """

    def setup(self):
        n_timesteps = self.options["plant_config"]["plant"]["simulation"]["n_timesteps"]
        self.config = SimpleNuclearReactorCostConfig.from_dict(
            merge_shared_inputs(self.options["tech_config"]["model_inputs"], "cost")
        )
        super().setup()
        self.add_input("nuclear_reactor_rated_capacity", val=self.config.nuclear_reactor_rated_capacity, units="kW")
        self.add_input("nuclear_reactor_upfront_cost", val=self.config.nuclear_reactor_upfront_cost, units="kW")
        self.add_input("nuclear_reactor_fixed_om_cost", val=self.config.nuclear_reactor_fixed_om_cost, units="kW")
        self.add_input("nuclear_reactor_variable_om_cost", val=self.config.nuclear_reactor_variable_om_cost, units="kW*h")
        self.add_input("electricity_out", val=1000, shape=n_timesteps, units="kW")

    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        # Capital Expenditure of Nuclear Reactor
        outputs["CapEx"] = inputs["nuclear_reactor_rated_capacity"] * inputs["nuclear_reactor_upfront_cost"]
        # Operational Expenditure of Nuclear Reactor 
        outputs["OpEx"] = (inputs["nuclear_reactor_fixed_om_cost"] * inputs["nuclear_reactor_rated_capacity"]) + 
            (inputs["nuclear_reactor_variable_om_cost"] * sum(inputs["electricity_out"][:]))


