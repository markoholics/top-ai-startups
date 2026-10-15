from . import ashby, greenhouse, lever, recruitee, workable

# SmartRecruiters is not here on purpose. Its documented posting APIs need a
# private API key, and the open endpoint is not in its documentation.
ADAPTERS = {m.NAME: m for m in (greenhouse, lever, ashby, workable, recruitee)}
