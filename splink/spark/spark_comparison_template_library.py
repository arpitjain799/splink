from ..comparison_template_library import (
    DateComparisonBase,
    NameComparisonBase,
    PostcodeComparisonBase,
)
from .spark_comparison_library import SparkComparisonProperties


class date_comparison(SparkComparisonProperties, DateComparisonBase):
    pass


class name_comparison(SparkComparisonProperties, NameComparisonBase):
    pass

class postcode_comparison(SparkComparisonProperties, PostcodeComparisonBase):
    pass
