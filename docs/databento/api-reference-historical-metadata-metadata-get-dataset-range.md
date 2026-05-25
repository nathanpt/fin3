### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.metadata.get_dataset_range![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Get the available range for the [dataset](/docs/api-reference-
historical/basics/datasets) given the user's entitlements.

Use this method to discover data availability. The `start` and `end` values in
the response can be used with the [timeseries.get_range](/docs/api-reference-
historical/timeseries/timeseries-get-range) and [batch.submit_job](/docs/api-
reference-historical/batch/batch-submit-job) endpoints.

This endpoint will return the `start` and `end` timestamps over the entire
dataset as well as the per-schema `start` and `end` timestamps under the
`schema` key. In some cases, a schema's availability is a subset of the entire
dataset availability.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

dataset

required | Dataset or str

The [dataset](/docs/api-reference-historical/basics/datasets) code (string
identifier). Must be one of the values from [list_datasets](/docs/api-
reference-historical/metadata/metadata-list-datasets).

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

`dict[str, str | dict[str, str]]`

The available range for the dataset.

start

str

The inclusive start of the available range as an [ISO 8601
timestamp](https://www.iso.org/iso-8601-date-and-time-format.html).

end

str

The exclusive end of the available range as an [ISO 8601
timestamp](https://www.iso.org/iso-8601-date-and-time-format.html).

schema

dict[str, str]

A mapping of schema names to per-schema `start` and `end` timestamps.

