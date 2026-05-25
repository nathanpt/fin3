### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.batch.submit_job![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Make a batch download job request for flat files.

Once a request is submitted, our system processes the request and prepares the
batch files in the background. The status of your request and the files can be
accessed from the [Download center](https://databento.com/portal/download-
center) from your user portal.

This method takes longer than a streaming request, but is advantageous for
larger requests as it supports delivery mechanisms that allow multiple
accesses of the data without additional cost for each subsequent download
after the first.

Related: [batch.list_jobs](/docs/api-reference-historical/batch/batch-list-
jobs).

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

dataset

required | Dataset or str

The [dataset](/docs/api-reference-historical/basics/datasets) code (string
identifier). Must be one of the values from [list_datasets](/docs/api-
reference-historical/metadata/metadata-list-datasets).

symbols

required | Iterable[str | int] or str or int

The product symbols to filter for. Takes up to 2,000 symbols per request. If
more than 1 symbol is specified, the data is merged and sorted by time. If
`'ALL_SYMBOLS'` or `None` then will select **all** symbols.

schema

required | Schema or str

The data record [schema](/docs/schemas-and-data-formats/whats-a-schema). Must
be one of the values from [list_schemas](/docs/api-reference-
historical/metadata/metadata-list-schemas).

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

encoding

optional | Encoding or str

The data [encoding](/docs/api-reference-historical/basics/encodings). Must be
one of 'dbn', 'csv', 'json'. For fastest transfer speed, 'dbn' is recommended.

compression

optional | Compression or str

The data [compression](/docs/api-reference-historical/basics/compression)
mode. Must be either 'zstd', 'none', or None. For fastest transfer speed,
'zstd' is recommended.

pretty_px

optional | bool, default False

If prices should be formatted to the correct scale (using the fixed-precision
scalar 1e-9). Only applicable for 'csv' or 'json' encodings.

pretty_ts

optional | bool, default False

If timestamps should be formatted as ISO 8601 strings. Only applicable for
'csv' or 'json' encodings.

map_symbols

optional | bool

If a symbol field should be included with each text-encoded record. If `None`,
will default to `True` for `csv` and `json` encodings and `False` for `dbn`.

split_symbols

optional | bool, default False

If files should be split by raw symbol. Cannot be requested with
`'ALL_SYMBOLS'`. Cannot be used with `limit`.

split_duration

optional | Duration or str, default 'day'

The maximum time duration before batched data is split into multiple files.
Must be one of 'day', 'week', 'month', or 'none'. A week starts on Sunday UTC.

split_size

optional | int

The maximum size (in bytes) of each batched data file before being split. Must
be an integer between 1e9 and 10e9 inclusive (1GB - 10GB). Defaults to no
split size.

delivery

optional | Delivery or str, default 'download'

The delivery mechanism for the batched data files once processed. Only
'download' is supported at this time.

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

The maximum number of records to return. If `None` then no limit. Cannot be
used with `split_symbols`.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

`dict[str, Any]`

The description of the submitted batch job.

id

str

The unique job ID for the request.

user_id

str

The user ID of the user who made the request.

api_key

str or None

The API key name for the request (if Basic Auth was used).

cost_usd

float or None

The cost of the job in US dollars (`None` until the job is done processing).

dataset

str

The [dataset](/docs/api-reference-historical/basics/datasets) code (string
identifier).

symbols

str

The list of symbols specified in the request.

stype_in

str

The [symbology type](/docs/api-reference-historical/basics/symbology) of input
`symbols`.

stype_out

str

The [symbology type](/docs/api-reference-historical/basics/symbology) of
output symbols.

schema

str

The data record [schema](/docs/schemas-and-data-formats/whats-a-schema).

start

str

The ISO 8601 timestamp start of request time range (inclusive).

end

str

The ISO 8601 timestamp end of request time range (exclusive).

limit

int or None

The maximum number of records to return.

encoding

str

The data [encoding](/docs/api-reference-historical/basics/encodings).

compression

str

The data [compression](/docs/api-reference-historical/basics/compression)
mode.

pretty_px

bool

If prices are formatted to the correct scale (using the fixed-precision scalar
1e-9).

pretty_ts

bool

If timestamps are formatted as ISO 8601 strings.

map_symbols

bool

If a symbol field is included with each text-encoded record.

split_symbols

bool

If files are split by raw symbol.

split_duration

str

The maximum time interval for an individual file before splitting into
multiple files.

split_size

int or None

The maximum size for an individual file before splitting into multiple files.

packaging

str or None

The packaging method of the batch data, one of 'none', 'zip', or 'tar'.

delivery

str

The delivery mechanism of the batch data. Only 'download' is supported at this
time.

record_count

int or None

The number of data records (`None` until the job is processed).

billed_size

int or None

The size of the raw binary data used to process the batch job (used for
billing purposes).

actual_size

int or None

The total size of the result of the batch job after splitting and compression.

package_size

int or None

The total size of the result of the batch job after any packaging (including
metadata).

state

str

The current status of the batch job. One of 'received', 'queued',
'processing', 'done', or 'expired'.

ts_received

str

The ISO 8601 timestamp when Databento received the batch job.

ts_queued

str or None

The ISO 8601 timestamp when the batch job was queued.

ts_process_start

str or None

The ISO 8601 timestamp when the batch job began processing (if it's begun).

ts_process_done

str or None

The ISO 8601 timestamp when the batch job finished processing (if it's
finished).

ts_expiration

str or None

The ISO 8601 timestamp when the batch job will expire from the [Download
center](/docs/portal/download-center).

progress

int or None

The progress percentage (0-100). Always `None` in submit responses.

