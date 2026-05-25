### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.timeseries.get_range![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Makes a streaming request for time series data from Databento.

This is the primary method for getting historical market data, instrument
definitions, and status data directly into your application.

This method only returns after all of the data has been downloaded, which can
take a long time. For large requests, consider using
[batch.submit_job](/docs/api-reference-historical/batch/batch-submit-job)
instead.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

dataset

required | Dataset or str

The [dataset](/docs/api-reference-historical/basics/datasets) code (string
identifier). Must be one of the values from [list_datasets](/docs/api-
reference-historical/metadata/metadata-list-datasets).

start

required | pd.Timestamp, datetime, date, str, or int

The inclusive start of the request range. Filters on `ts_recv` if it exists in
the schema, otherwise `ts_event`. Takes
[pd.Timestamp](https://pandas.pydata.org/pandas-
docs/stable/reference/api/pandas.Timestamp.html), [Python
datetime](https://docs.python.org/3/library/datetime.html#datetime-objects),
[Python date](https://docs.python.org/3/library/datetime.html#date-objects),
[ISO 8601](https://www.iso.org/iso-8601-date-and-time-format.html) string, or
[UNIX timestamp](https://en.wikipedia.org/wiki/Unix_time) in nanoseconds.
Assumes UTC as timezone unless otherwise specified.

end

optional | pd.Timestamp, datetime, date, str, or int

The exclusive end of the request range. Filters on `ts_recv` if it exists in
the schema, otherwise `ts_event`. Takes
[pd.Timestamp](https://pandas.pydata.org/pandas-
docs/stable/reference/api/pandas.Timestamp.html), [Python
datetime](https://docs.python.org/3/library/datetime.html#datetime-objects),
[Python date](https://docs.python.org/3/library/datetime.html#date-objects),
[ISO 8601](https://www.iso.org/iso-8601-date-and-time-format.html) string, or
[UNIX timestamp](https://en.wikipedia.org/wiki/Unix_time) in nanoseconds.
Assumes UTC as timezone unless otherwise specified. Defaults to the [forward
filled](/docs/standards-and-conventions/common-fields-enums-types#forward-
filling-end-parameters) value of `start` based on the resolution provided.

symbols

optional | Iterable[str | int] or str or int

The product symbols to filter for. Takes up to 2,000 symbols per request. If
more than 1 symbol is specified, the data is merged and sorted by time. If
`'ALL_SYMBOLS'` or `None` then will select **all** symbols.

schema

optional | Schema or str, default 'trades'

The data record [schema](/docs/schemas-and-data-formats/whats-a-schema). Must
be one of the values from [list_schemas](/docs/api-reference-
historical/metadata/metadata-list-schemas).

stype_in

optional | SType or str, default 'raw_symbol'

The [symbology type](/docs/api-reference-historical/basics/symbology) of input
`symbols`. Must be one of 'raw_symbol', 'instrument_id', 'parent', or
'continuous'.

stype_out

optional | SType or str, default 'instrument_id'

The [symbology type](/docs/api-reference-historical/basics/symbology) of
output symbols. Must be one of 'raw_symbol', 'instrument_id', 'parent', or
'continuous'. Must be a valid symbology combination with `stype_in`. See
[symbology combinations](/docs/standards-and-conventions/symbology#supported-
symbology-combinations).

limit

optional | int

The maximum number of records to return. If `None` then no limit.

path

optional | PathLike[str] or str

The file path to stream the data to. It is recommended to use the ".dbn.zst"
suffix.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

A [DBNStore](/docs/api-reference-historical/helpers/dbn-store) object.

A full list of fields for each schema is available through
[Historical.metadata.list_fields](/docs/api-reference-
historical/metadata/metadata-list-fields).

