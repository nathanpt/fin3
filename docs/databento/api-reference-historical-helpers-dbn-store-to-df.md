### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBNStore.to_df![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Converts data to a [pandas](https://pandas.pydata.org/) DataFrame.

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> The DataFrame index will be set to `ts_recv` if it exists in the schema,
> otherwise it will be set to `ts_event`.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> While not optimized for use with live data due to their column-oriented
> format, pandas DataFrames can still be used with live data by first
> streaming DBN data to a file, then converting to a DataFrame with
> DBNStore.from_file().to_df(). See [this example](/docs/examples/basics-
> live/live-stream-to-file) for more information.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

price_type

optional | PriceType or str, default "float"

The price type to use for price fields. If "fixed", prices will have a type of
`int` in fixed decimal format; each unit representing 1e-9 or 0.000000001. If
"float", prices will have a type of `float`. If "decimal", prices will be
instances of `decimal.Decimal`.

pretty_ts

optional | bool, default True

Whether timestamp columns are converted to tz-aware `pandas.Timestamp`. The
timezone can be specified using the `tz` parameter.

map_symbols

optional | bool, default True

If symbology mappings from the metadata should be used to create a 'symbol'
column, mapping the instrument ID to its raw symbol for every record.

schema

optional | Schema or str

The data record [schema](/docs/schemas-and-data-formats/whats-a-schema) for
the output DataFrame. Must be one of the values from [list_schemas](/docs/api-
reference-historical/metadata/metadata-list-schemas). This is only required
when reading a `DBNStore` with mixed record types.

tz

optional | datetime.tzinfo or str, default UTC

If `pretty_ts` is `True`, all timestamps will be converted to the specified
timezone.

count

optional | int

If set, instead of returning a single `DataFrame` a `DataFrameIterator`
instance will be returned. When iterated, this object will yield a `DataFrame`
with at most `count` elements until the entire contents of the `DBNStore` are
exhausted.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

A pandas [DataFrame](https://pandas.pydata.org/docs/reference/frame.html)
object.

