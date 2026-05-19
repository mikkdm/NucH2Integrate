import matplotlib.pyplot as plt

from h2integrate.core.h2integrate_model import H2IntegrateModel


# Create a GreenHEART model
h2i = H2IntegrateModel("nuclear_reactor_thermal_htse.yaml")

# Run the model
h2i.run()

h2i.post_process()

fig, ax = plt.subplots(3)
ax[0].plot(h2i.prob.get_val("nuclear.electricity_out", units="MW"))
ax[1].plot(h2i.prob.get_val("htse.hydrogen_out", units="kg/h"))
ax[2].plot(h2i.prob.get_val("h2_storage.SOC", units="percent"))

plt.show()
