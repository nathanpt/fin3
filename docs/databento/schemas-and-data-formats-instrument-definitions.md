Schemas and data formats

# Instrument
definitions

**Instrument definitions** provide point-in-time reference information about
each instrument, which includes properties like symbol, instrument name,
expiration date, listing date, tick size, and strike price.

## Fields
(`definition`)

Field  | Type  | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch.  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch.  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 19 in the definition schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue.  
`instrument_id` | uint32_t | The numeric instrument ID.  
`raw_symbol` | char[symbol_cstr_len] | The instrument name (symbol) provided by the publisher, where `symbol_cstr_len` is specified in the [Metadata](/docs/standards-and-conventions/databento-binary-encoding#metadata).  
`security_update_action` | char | Indicates if the instrument definition has been **A** dded, **M** odified, or **D** eleted.  
`instrument_class` | char | The classification of the instrument. See [Instrument class](/docs/schemas-and-data-formats/instrument-definitions#instrument-class).  
`min_price_increment` | int64_t | The minimum constant tick for the instrument in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`display_factor` | int64_t | The multiplier to convert the venue’s display price to the conventional price in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`expiration` | uint64_t | The last eligible trade time expressed as a number of nanoseconds since the UNIX epoch. May only have date precision depending on the publisher.  
`activation` | uint64_t | The time of instrument activation expressed as a number of nanoseconds since the UNIX epoch. May only have date precision depending on the publisher.  
`high_limit_price` | int64_t | The allowable high limit price for the trading day in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`low_limit_price` | int64_t | The allowable low limit price for the trading day in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`max_price_variation` | int64_t | The differential value for price banding in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`unit_of_measure_qty` | int64_t | The contract size for each instrument, in combination with `unit_of_measure`, in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`min_price_increment_amount` | int64_t | The value currently under development by the venue. Converted to units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`price_ratio` | int64_t | The value used for price calculation in spread and leg pricing in units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`inst_attrib_value` | int32_t | A bitmap of instrument eligibility attributes.  
`underlying_id` | uint32_t | The `instrument_id` of the first underlying instrument.  
`raw_instrument_id` | uint64_t | The instrument ID assigned by the publisher. May be the same as `instrument_id`.  
`market_depth_implied` | int32_t | The implied book depth on the price level data feed.  
`market_depth` | int32_t | The (outright) book depth on the price level data feed.  
`market_segment_id` | uint32_t | The market segment of the instrument.  
`max_trade_vol` | uint32_t | The maximum trading volume for the instrument.  
`min_lot_size` | int32_t | The minimum order entry quantity for the instrument.  
`min_lot_size_block` | int32_t | The minimum quantity required for a block trade of the instrument.  
`min_lot_size_round_lot` | int32_t | The minimum quantity required for a round lot of the instrument. Multiples of this quantity are also round lots.  
`min_trade_vol` | uint32_t | The minimum trading volume for the instrument.  
`contract_multiplier` | int32_t | The number of deliverables per instrument, i.e. peak days.  
`decay_quantity` | int32_t | The quantity that a contract will decay daily, after `decay_start_date` has been reached.  
`original_contract_size` | int32_t | The fixed contract value assigned to each instrument.  
`appl_id` | int16_t | The channel ID assigned at the venue.  
`maturity_year` | uint16_t | The calendar year reflected in the instrument symbol.  
`decay_start_date` | uint16_t | The date at which a contract will begin to decay.  
`channel_id` | uint16_t | The channel ID assigned by Databento as an incrementing integer starting at zero.  
`currency` | char[4] | The currency used for price fields.  
`settl_currency` | char[4] | The currency used for settlement, if different from `currency`.  
`secsubtype` | char[6] | The strategy type of the spread.  
`group` | char[21] | The security group code of the instrument.  
`exchange` | char[5] | The exchange used to identify the instrument.  
`asset` | char[11] | The underlying asset code (product code) of the instrument.  
`cfi` | char[7] | The ISO standard instrument categorization code.  
`security_type` | char[7] | The type of the instrument, e.g. FUT for future or future spread.  
`unit_of_measure` | char[31] | The unit of measure for the instrument’s original contract size, e.g. USD or LBS.  
`underlying` | char[21] | The symbol of the first underlying instrument.  
`strike_price_currency` | char[4] | The currency used for `strike_price`.  
`strike_price` | int64_t | The exercise price if the instrument is an option. Converted to units of 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
`match_algorithm` | char | The matching algorithm used for the instrument, typically **F** IFO. See [Matching algorithm](/docs/schemas-and-data-formats/instrument-definitions#matching-algorithm).  
`main_fraction` | uint8_t | The price denominator of the main fraction.  
`price_display_format` | uint8_t | The number of digits to the right of the tick mark, to display fractional prices.  
`sub_fraction` | uint8_t | The price denominator of the sub fraction.  
`underlying_product` | uint8_t | The product complex of the instrument.  
`maturity_month` | uint8_t | The calendar month reflected in the instrument symbol.  
`maturity_day` | uint8_t | The calendar day reflected in the instrument symbol, or 0.  
`maturity_week` | uint8_t | The calendar week reflected in the instrument symbol, or 0.  
`user_defined_instrument` | char | Indicates if the instrument is user defined: **Y** es or **N** o.  
`contract_multiplier_unit` | int8_t | The type of `contract_multiplier`. Either `1` for hours, or `2` for days.  
`flow_schedule_type` | int8_t | The schedule for delivering electricity.  
`tick_rule` | uint8_t | The tick rule of the spread.  
`leg_count` | uint16_t | The number of legs in the strategy or spread. Will be 0 for outrights.  
`leg_index` | uint16_t | The 0-based index of the leg (if any).  
`leg_instrument_id` | uint32_t | The numeric ID assigned to the leg instrument.  
`leg_raw_symbol` | char[symbol_cstr_len] | The leg instrument's raw symbol assigned by the publisher, where `symbol_cstr_len` is specified in the [Metadata](/docs/standards-and-conventions/databento-binary-encoding#metadata).  
`leg_instrument_class` | char | The leg instrument's classification. See [Instrument class](/docs/standards-and-conventions/common-fields-enums-types#instrument-class).  
`leg_side` | char | The side taken for the leg when buying the spread or strategy.  
`leg_price` | int64_t | The tied price (if any) of the leg. Used for hedged strategies.  
`leg_delta` | int64_t | The associated delta (if any) of the leg. Used for hedged strategies.  
`leg_ratio_price_numerator` | int32_t | The numerator of the price ratio of the leg within the spread.  
`leg_ratio_price_denominator` | int32_t | The denominator of the price ratio of the leg within the spread.  
`leg_ratio_qty_numerator` | int32_t | The numerator of the quantity ratio of the leg within the spread.  
`leg_ratio_qty_denominator` | int32_t | The denominator of the quantity ratio of the leg within the spread.  
`leg_underlying_id` | uint32_t | The numeric ID of the leg instrument's underlying instrument.  
  
## Instrument
class

The `instrument_class` field groups instruments into different classes of
securities and derivatives.

Name | Value | Description  
---|---|---  
Bond | `B` | A bond.  
Call | `C` | A call option.  
Future | `F` | A future.  
Stock | `K` | A stock.  
Mixed spread | `M` | A spread with legs of multiple instrument classes.  
Put | `P` | A put option.  
Future spread | `S` | A spread with future legs.  
Option spread | `T` | A spread with options.  
FX spot | `X` | A foreign exchange spot.  
Commodity spot | `Y` | A commodity being traded for immediate delivery.  
  
## Security
type

The `security_type` field classifies the type of instrument.

Name | Value | Description  
---|---|---  
Equity | `EQUITY` | An equity.  
Future | `FUT` | A future or a spread on futures.  
Interest rate swap | `IRS` | An interest rate swap security.  
Spreads | `MLEG` | A spread with legs of multiple instrument classes.  
Option on equity | `OPT` | An option on equity or a spread with options on equities.  
Option on future | `OOF` | An option on future or a spread with options on futures.  
Spot | `SPOT` | An FX spot or a commodity spot.  
  
## Matching
algorithm

The `matching_algorithm` field defines the type of matching algorithm used for
the instrument.

Name | Value | Description  
---|---|---  
Undefined | ` ` | A matching algorithm was not specified.  
FIFO | `F` | First-in-first-out matching.  
Configurable | `K` | A configurable match algorithm.  
Pro-Rata | `C` | Trade quantity is allocated to resting orders based on a pro-rata percentage: resting order quantity divided by total quantity.  
FIFO with LMM | `T` | Like FIFO, but with LMM allocations prior to FIFO allocations.  
Threshold Pro-Rata | `O` | Like Pro-Rata, but includes a configurable allocation to the first order that improves the market.  
FIFO with Top Order and LMM | `S` | Like FIFO with LMM, but includes a configurable allocation to the first order that improves the market.  
Threshold Pro-Rata with LMM | `Q` | Like Threshold Pro-Rata, but includes a special priority to LMMs.  
Eurodollar Futures | `Y` | Special variant used only for Eurodollar futures on CME.  
Time Pro-Rata | `P` | A commodity being traded for immediate delivery.  
Institutional Prioritization | `V` | A two-pass FIFO algorithm. The first pass fills the Institutional Group the aggressing order is associated with. The second pass matches orders without an Institutional Group association.  
  
## Point-in-time
instrument definitions![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

An important distinction of Databento's instrument definition data is that we
usually provide it in **point-in-time** format like the rest of our market
data.

In other words, the instrument definitions are timestamped to the nanosecond
and treated as a time series, allowing you to simulate the sequence in which
they arrived in real time. This is useful as it avoids lookahead and
survivorship bias, and also allows you to backtest any strategy that trades
instruments immediately as they become available intraday, such as new options
strikes and new listings from IPOs.

If you're switching to Databento from another data provider, you may find this
behavior different, as many data providers only give you a cumulative list of
all instruments or daily updates on instrument definitions.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Snapshots![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

For publishers that do not provide a full list of instrument definitions at
least daily, the historical API provides a snapshot of all active definitions
at UTC midnight Monday through Friday. Therefore, any request for definitions
for a 24-hour period beginning at UTC midnight is guaranteed to include
definitions for all instruments active during that time period.

## Instrument
definitions on time series endpoints vs. reference data
endpoints

Databento also provides other forms of reference information and static data,
such as corporate actions, cross-symbology mappings, and security master data.
However, these additional forms of reference data may be found on API
endpoints other than our time series endpoints.

The reason that these are separated into different endpoints is that
instrument definitions on our time series endpoints are those included with
the primary source of data, which is usually a direct feed from a trading
venue. On the other hand, other forms of reference data may originate from
different secondary sources or need to be licensed separately from such direct
feeds.

Databento generally provides ways for you to join and merge instrument
definitions and other forms of reference data.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Strategies![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

One definition record is created for each leg of a strategy or spread. The
primary fields will be the same for each of these records, while the fields
beginning with `leg_` will have information about the particular leg.

