### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBNStore.to_parquet![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Write data to a file in [Apache parquet](https://parquet.apache.org/) format.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

path

required | PathLike[str] or str

The file path to write the data to.

price_type

optional | PriceType or str, default "float"

The price type to use for price fields. If "fixed", prices will have a type of
`int` in fixed decimal format; each unit representing 1e-9 or 0.000000001. If
"float", prices will have a type of `float`.

pretty_ts

optional | bool, default True

Whether timestamp columns are converted to tz-aware `pyarrow.TimestampType`
(UTC).

map_symbols

optional | bool, default True

If symbology mappings from the metadata should be used to create a 'symbol'
column, mapping the instrument ID to its raw symbol for every record.

schema

optional | Schema or str

The data record [schema](/docs/schemas-and-data-formats/whats-a-schema) for
the output parquet file. Must be one of the values from
[list_schemas](/docs/api-reference-historical/metadata/metadata-list-schemas).
This is only required when reading a `DBNStore` with mixed record types.

mode

optional | str

The file write mode to use, either "x" or "w". Defaults to "w".

**kwargs

optional | Any

Keyword arguments to pass to
[pyarrow.parquet.ParquetWriter](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html).
These can be used to override the default behavior of the writer.

