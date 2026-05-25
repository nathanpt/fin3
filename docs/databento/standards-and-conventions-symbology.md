Standards and conventions

# ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Financial datasets usually contain symbols or
[product](/docs/faqs/instruments-and-products) identifiers. The mapping of
symbols to their corresponding product names can be extracted from our
[definition schema](/docs/schemas-and-data-formats/instrument-definitions) as
well as the metadata packaged with our data.

Databento supports four symbology types, also referred to as **stypes**. They
are: [raw_symbol](/docs/standards-and-conventions/symbology#raw-symbol),
[instrument_id](/docs/standards-and-conventions/symbology#instrument-id),
[parent](/docs/standards-and-conventions/symbology#parent), and
[continuous](/docs/standards-and-conventions/symbology#continuous).

We include methods for mapping between symbology types and resolving symbols
under the [symbology](/docs/api-reference-historical/symbology) family of
methods.

We do not retroactively reassign symbols in our historical data. Symbols found
in our historical data are exactly as they appeared in the live data at the
original event time. For example, if a stock symbol was changed due to a
corporate action, we preserve the original symbol for data before the event
and the new symbol for data after the event. This approach guarantees that the
historical data looks identical to the live data at the original time, and
encourages our users to write their integration in a manner that handles
historical and live data in the same way.

A symbol can be reused and point to two different instruments on two different
dates.

## Supported
symbology combinations![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

When requesting data, such as with the [timeseries.get_range](/docs/api-
reference-historical/timeseries/timeseries-get-range) or
[batch.submit_job](/docs/api-reference-historical/batch/batch-submit-job)
endpoints, an input (`stype_in`) and output (`stype_out`) symbology type are
specified. Not all symbology types are supported for output and some symbology
types are not available in certain datasets.

`stype_in` ↓ / `stype_out` → | `instrument_id` | `raw_symbol` | `parent` | `continuous`  
---|---|---|---|---  
`instrument_id` | ✓ | ✓ |  |   
`raw_symbol` | ✓ |  |  |   
`parent` | ✓ |  |  |   
`continuous` | ✓ |  |  |   
  
All datasets support bidirectional conversion between `raw_symbol` and
`instrument_id`.

The table below outlines the datasets that support [parent
symbology](/docs/standards-and-conventions/symbology#parent) and [continuous
contract symbology](/docs/standards-and-conventions/symbology#continuous)
(futures contracts only).

`stype_in` | `parent` | `continuous`  
---|---|---  
[GLBX.MDP3](/docs/venues-and-datasets/glbx-mdp3) | ✓ | ✓  
[IFEU.IMPACT](/docs/venues-and-datasets/ifeu-impact) | ✓ | ✓  
[IFLL.IMPACT](/docs/venues-and-datasets/ifll-impact) | ✓ | ✓  
[IFUS.IMPACT](/docs/venues-and-datasets/ifus-impact) | ✓ | ✓  
[NDEX.IMPACT](/docs/venues-and-datasets/ndex-impact) | ✓ | ✓  
[OPRA.PILLAR](/docs/venues-and-datasets/opra-pillar) | ✓ |   
[XCBF.PITCH](/docs/venues-and-datasets/xcbf-pitch) | ✓ | ✓  
[XEEE.EOBI](/docs/venues-and-datasets/xeee-eobi) | ✓ | ✓  
[XEUR.EOBI](/docs/venues-and-datasets/xeur-eobi) | ✓ | ✓  
  
## Raw
symbol

Raw symbols are the original string symbols used by the publisher in the
source data. This can be useful for environments with direct market
connectivity. Examples of raw symbols include `AAPL`, `ESH3`, etc.

This symbology is used by setting the `stype_in=raw_symbol` parameter in the
API.

## Instrument
ID

Instrument IDs are the unique numeric ID assigned to each instrument by the
publisher. Most venues use such numeric IDs under the hood. Numeric IDs have
the benefit of taking less space than most string symbols. However, numeric
IDs can be difficult to work with, especially as some publishers remap them
daily.

This symbology is used by setting the `stype_in=instrument_id` parameter in
the API.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parent![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Parent symbology is a smart symbology feature that allows you to easily refer
to groups of related symbols using a single root symbol. The root symbols are
sourced from the `asset` field of the [definition schema](/docs/schemas-and-
data-formats/instrument-definitions). All futures for a root symbol can be
referenced using the parent symbol `[ROOT].FUT`, for options: `[ROOT].OPT`.
For example, `ES.FUT` refers to all E-mini S&P 500 futures and futures spreads
and `ES.OPT` refers to all quarterly E-mini S&P 500 options and option
spreads.

The type of instrument will be specified in the `instrument_class` field. When
requesting data using futures parent symbology, this field will indicate
whether the instrument is a future or futures spread. When requesting data
using options parent symbology, it will indicate whether the instrument is a
call or put. A full list of variants can be found in the [instrument class
documentation](/docs/schemas-and-data-formats/instrument-
definitions#instrument-class).

This symbology is used by setting the `stype_in=parent` parameter in the API.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Continuous![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> Our continuous contract symbology is a notation that maps to an actual,
> tradable instrument on any given date. The continuous contract prices
> returned are the original, unadjusted prices. We don't create a synthetic
> time series by back-adjusting the prices to remove jumps during rollovers.

Continuous contract symbology is a smart symbology feature that allow a single
symbol to refer to different instruments over time. For example, continuous
contract symbology allows you to query a single symbol that changes or _rolls_
forward before expiration.

For futures outrights, we use the format `[ROOT].[ROLL_RULE].[RANK]` to refer
to continuous contracts that change over time according to a roll rule and
rank. Like with parent symbology, the root symbol corresponds with the `asset`
field of the [definition schema](/docs/schemas-and-data-formats/instrument-
definitions).

`RANK` is a zero-indexed integer, and `ROLL_RULE` is either `c`, `n`, or `v`
from the table below.

This symbology is used by setting the `stype_in=continuous` parameter in the
API. It is not currently possible to select continuous contracts through our
web portal.

Roll rule | Code | Overview | Example  
---|---|---|---  
Calendar | `c` | Refers to the offset from the closest expiration or front month. | On September 28, 2022 `NG.c.0` referred to the October NG future (NGV2) and `NG.c.1` referred to the November future (NGX2). However, because the October contract expired at the end of trading on September 28 and the continuous smart symbol would be rolled forward, on September 29, 2022, `NG.c.0` then referred to the November future (NGX2) and `NG.c.1` referred to the December future (NGZ2).  
Open interest | `n` | Will rank the expirations by the open interest at the previous day's close. | `CL.n.1` refers to the CL future with the second-highest open interest.  
Volume | `v` | Will rank the expirations by the previous day's trading volume. | `ZN.v.0` refers to the ZN future with the most volume.  
  
## All
symbols

It is possible to request all symbols within a dataset without providing them
explicitly. This is done by specifying `ALL_SYMBOLS` with
`stype_in=raw_symbol` or `stype_in=parent` in the API.

When requesting all symbols using [timeseries.get_range](/docs/api-reference-
historical/timeseries/timeseries-get-range) symbology data is not provided.
This means that for the CSV and JSON encodings the parameter
`map_symbols=True` is not allowed. For the DBN encoding, the metadata header
will not contain symbology mappings.

When requesting all symbols using [batch.submit_job](/docs/api-reference-
historical/batch/batch-submit-job), the
[symbology.json](/docs/portal/download-center#support-files) support file will
not contain symbology mappings.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Symbology.resolve
endpoint

Databento offers symbology resolution for free in our [symbology.resolve
endpoint](/docs/api-reference-historical/symbology/symbology-resolve) and in
our client libraries. This endpoint can be used to request mappings from one
symbology type to another and contains all the data necessary to perform these
conversions.

Field | Description  
---|---  
`result` | The symbology mapping result. For each requested symbol, a list of symbology mappings is provided.  
`symbols` | The requested symbols.  
`stype_in` | The requested input symbology type.  
`stype_out` | The requested output symbology type.  
`start_date` | The requested symbology start date, as an ISO 8601 date string.  
`end_date` | The requested symbology end date, as an ISO 8601 date string.  
`partial` | The list of symbols, if any, that partially resolved inside the start date and end date interval.  
`not_found` | The list of symbols, if any, that failed to resolve inside the start date and end date interval.  
`message` | A short message indicating the overall symbology result. Can be one of: "OK", "Not found", or "Partially resolved".  
`status` | A numerical status field indicating the overall symbology result. Can be one of: 0 (OK), 1 (Partially resolved), or 2 (Not found).  
  
## Symbology
support file

For some batch downloads, symbology information for the job is contained in
`*.symbology.json` support files. This file is automatically included when the
batch job files do not contain symbology information, such as when requesting
CSV or JSON encodings when symbol mapping is not requested. Below is a sample
file. It's contents are directly obtained from the
[symbology.resolve](/docs/api-reference-historical/symbology/symbology-
resolve) endpoint:

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    {
       "result": {
          "ES.c.0": [
             { "d0": "2023-01-01", "d1": "2023-03-19", "s": "206299"},
             { "d0": "2023-03-19", "d1": "2023-06-01", "s": "95414"}
          ]
       },
       "symbols": ["ES.c.0"],
       "stype_in": "continuous",
       "stype_out": "instrument_id",
       "start_date": "2023-01-01",
       "end_date": "2023-06-01",
       "partial": [],
       "not_found": [],
       "message": "OK",
       "status": 0
    }
    

Examining this sample we can see a requested mapping of the `stype_in`
("continuous") `symbol` ("ES.c.0") to `stype_out` ("instrument_id") over the
date range `start_date` ("2023-01-01") to the `end_date` ("2023-06-01").

We can check the `message` ("OK") and `status` (0) fields to confirm that our
request was successful over the entire date interval. Additionally, the
`not_found` and `partial` fields are empty.

Most importantly, the `result` field contains our symbology mappings keyed by
each input `symbol`. Each `symbol` entry in the `result` mapping will contain
a list of entries. These entries contain a start date in the `d0` field, and
an end date in the `d1` field for the mapping. The `s` field contains the
output symbol.

Continuous contract | Start date (d0) | End date (d1) | Instrument ID (s)  
---|---|---|---  
ES.c.0 | 2023-01-01 | 2023-03-19 | 206299  
ES.c.0 | 2023-03-19 | 2023-06-01 | 95414  
  
## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)SymbolMappingMsg![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento's live data publishes symbology information using the
`SymbolMappingMsg`. This message will always contain the input symbol and the
resolved output symbol. The record header of the `SymbolMappingMsg` will
always contain the `instrument_id`. See our [DBN encoding](/docs/standards-
and-conventions/databento-binary-encoding) article for more information on our
binary format.

Field | Type | Description  
---|---|---  
`stype_in` | uint8_t | The input symbology type (DBN version 2 only).  
`stype_in_symbol` | char[symbol_cstr_len] | The input symbol from the subscription, where `symbol_cstr_len` is specified in the [Metadata](/docs/standards-and-conventions/databento-binary-encoding#metadata).  
`stype_out` | uint8_t | The output symbology type (DBN version 2 only). Will always be `raw_symbol`.  
`stype_out_symbol` | char[symbol_cstr_len] | The output symbol from the subscription, where `symbol_cstr_len` is specified in the [Metadata](/docs/standards-and-conventions/databento-binary-encoding#metadata).  
`start_ts` | uint64_t | The start of the mapping interval expressed as the number of nanoseconds since the UNIX epoch.  
`end_ts` | uint64_t | The end of the mapping interval expressed as the number of nanoseconds since the UNIX epoch.

