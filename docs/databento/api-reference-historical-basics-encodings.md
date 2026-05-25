### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Encodings![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

**DBN**

Databento Binary Encoding ([DBN](/docs/standards-and-conventions/databento-
binary-encoding)) is an extremely fast message encoding and highly-
compressible storage format for normalized market data. It includes a self-
describing metadata header and adopts a binary format with zero-copy
serialization.

We recommend using our [Python](/docs/api-reference-historical/helpers/dbn-
store?historical=python), [C++](/docs/api-reference-historical/helpers/dbn-
store?historical=cpp), or [Rust client libraries](/docs/api-reference-
historical/helpers/dbn-store?historical=rust) to read DBN files locally. A
[CLI tool](https://crates.io/crates/dbn-cli) is also available for converting
DBN files to CSV or JSON.

**CSV**

Comma-separated values (CSV) is a simple text file format for tabular data,
CSVs can be easily opened with Excel, loaded into
[pandas](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html)
data frames, or parsed in C++.

Our CSVs have one header line, followed by one record per line. Lines use
UNIX-style `\n` separators.

**JSON**

[JavaScript Object Notation](https://www.json.org/json-en.html) (JSON) is a
flexible text file format with broad language support and wide adoption across
web apps.

Our JSON files follow the [JSON lines specification](https://jsonlines.org/),
where each line of the file is a JSON record. Lines use UNIX-style `\n`
separators.

