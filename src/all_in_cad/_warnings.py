"""Package-scoped warning filters, imported first by ``all_in_cad``."""

import warnings

# Several wire models keep a JSON field literally named "schema" (it is part of the persisted
# evidence/report format). Pydantic warns that it shadows the deprecated BaseModel.schema()
# classmethod; the field is intentional, so silence exactly that warning for this package only.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" in "[A-Za-z_]+" shadows an attribute in parent "BaseModel"',
    category=UserWarning,
    module=r"all_in_cad(\..*)?$",
)
