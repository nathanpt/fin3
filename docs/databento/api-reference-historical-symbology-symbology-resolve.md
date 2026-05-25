### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.symbology.resolve![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Resolve a list of symbols from an input [symbology type](/docs/api-reference-
historical/basics/symbology), to an output symbology type.

Take, for example, a raw symbol to an instrument ID: `ESM2` → `3403`.

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

The symbols to resolve. Takes up to 2,000 symbols per request. Use
`'ALL_SYMBOLS'` to request **all** symbols (not available for every dataset).

stype_in

required | SType or str

The [symbology type](/docs/api-reference-historical/basics/symbology) of input
`symbols`. Must be one of 'raw_symbol', 'instrument_id', 'parent', or
'continuous'.

stype_out

required | SType or str

The [symbology type](/docs/api-reference-historical/basics/symbology) of
output symbols. Must be one of 'raw_symbol', 'instrument_id', 'parent', or
'continuous'. Must be a valid symbology combination with `stype_in`. See
[symbology combinations](/docs/standards-and-conventions/symbology#supported-
symbology-combinations).

start_date

required | date or str

The inclusive UTC start date of the request range as a [Python
date](https://docs.python.org/3/library/datetime.html#date-objects) or [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string.

end_date

optional | date or str

The exclusive UTC end date of the request range as a [Python
date](https://docs.python.org/3/library/datetime.html#date-objects) or [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string.
Defaults to the [forward filled](/docs/standards-and-conventions/common-
fields-enums-types#forward-filling-end-parameters) value of `start` based on
the resolution provided.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

`dict[str, Any]`

The results for the symbology resolution.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> For more information on symbology resolution, visit our [symbology
> documentation](/docs/standards-and-conventions/symbology#symbology-resolve-
> endpoint).

result

dict[str, list[dict[str, str]]

The symbology mapping result. For each requested symbol, a list of symbology
mappings is provided.

symbols

list[str]

The requested symbols.

stype_in

str

The requested input symbology type.

stype_out

str

The requested output symbology type.

start_date

str

The requested symbology start date as an [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string.

end_date

str

The requested symbology end date as an [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string.

partial

list[str]

The list of symbols, if any, that partially resolved inside the start date and
end date interval.

not_found

list[str]

The list of symbols, if any, that failed to resolve inside the start date and
end date interval.

message

str

A short message indicating the overall symbology result. Can be one of: "OK",
or "Partially resolved", or "Not found"

status

int

A numerical status field indicating the overall symbology result. Can be one
of: 0 (OK), 1 (Partially resolved), or 2 (Not found).

