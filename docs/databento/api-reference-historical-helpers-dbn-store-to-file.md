### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBNStore.to_file![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Write data to a DBN file.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

path

required | PathLike[str] or str

The file path to write to.

mode

optional | str

The file write mode to use, either "x" or "w". Defaults to "w".

compression

optional | Compression or str

The compression format to write. If `None`, uses the same compression as the
underlying data.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

A [DBNStore](/docs/api-reference-historical/helpers/dbn-store) object.

