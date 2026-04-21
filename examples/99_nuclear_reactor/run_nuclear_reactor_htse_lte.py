from h2integrate.core.h2integrate_model import H2IntegrateModel


# Create a GreenHEART model
h2i = H2IntegrateModel("nuclear_reactor_htse_lte.yaml")

# Run the model
h2i.run()

h2i.post_process()
