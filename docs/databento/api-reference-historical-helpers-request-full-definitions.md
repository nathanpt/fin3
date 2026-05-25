### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBNStore.request_full_definitions![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Request for full instrument [Definition](/docs/schemas-and-data-
formats/instrument-definitions)(s) for all symbols based on the metadata
properties. This is useful for retrieving the instrument definitions for saved
DBN data.

A [timeseries.get_range](/docs/api-reference-historical/timeseries/timeseries-
get-range) request is made to obtain the definitions data which will incur a
cost.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

client

required | Historical

The historical client to use for the request (contains the API key).

path

optional | PathLike[str] or str

The path to stream the data to on disk (will then return a `DBNStore`).

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

A [DBNStore](/docs/api-reference-historical/helpers/dbn-store) object.

A full list of fields for each schema is available through
[Historical.metadata.list_fields](/docs/api-reference-
historical/metadata/metadata-list-fields).

