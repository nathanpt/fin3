### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBNStore![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

The `DBNStore` object is an I/O helper class for working with
[DBN](/docs/standards-and-conventions/databento-binary-encoding)-encoded data.
Typically, this object is created when performing historical requests.
However, it can be created directly using DBN data on disk or in memory using
provided factory methods:

  * [DBNStore.from_bytes](/docs/api-reference-historical/helpers/dbn-store-from-bytes)
  * [DBNStore.from_file](/docs/api-reference-historical/helpers/dbn-store-from-file)

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Attributes![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

nbytes

int

The size of the data in bytes.

raw

bytes

The raw data from the I/O stream.

metadata

Metadata

The metadata header for the `DBNStore`.

dataset

str

The dataset ID.

schema

Schema or None

The data record schema. If `None`, the `DBNStore` may contain multiple
schemas.

symbols

list[str]

The query symbols for the data.

stype_in

SType or None

The query input symbology type for the data. If `None`, the `DBNStore` may
contain mixed STypes.

stype_out

SType

The query output symbology type for the data.

start

pd.Timestamp

The query start for the data as a
[pd.Timestamp](https://pandas.pydata.org/pandas-
docs/stable/reference/api/pandas.Timestamp.html).

end

pd.Timestamp or None

The query end for the data as a
[pd.Timestamp](https://pandas.pydata.org/pandas-
docs/stable/reference/api/pandas.Timestamp.html). If `None`, the `DBNStore`
data was created without a known end time.

limit

int or None

The query limit for the data.

encoding

Encoding

The data encoding.

compression

Compression

Return the data compression format (if any).

mappings

dict[str, list[dict[str, Any]]]

Return the symbology mappings for the data.

symbology

dict[str, Any]

Return the symbology resolution information for the data.

