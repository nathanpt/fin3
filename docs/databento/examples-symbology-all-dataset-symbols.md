Examples and tutorials

Symbology

# Dataset
symbols

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example we will show how to get all symbols for a dataset. We will use
the [Historical client](/docs/api-reference-historical/client) to get all
symbols available on a specific date. We will also use the [Live
client](/docs/api-reference-live/client/live) to get all symbols active for
the current session.

## Definition
schema

We will use the [definition schema](/docs/schemas-and-data-formats/instrument-
definitions). We will create a map of `raw_symbol` to `InstrumentDefMsgs`.

An `InstrumentDefMsg` contains fields such as:

  * `raw_symbol` \- The instrument name (symbol) provided by the publisher
  * `instrument_class` \- The classification of the instrument
  * `expiration` \- The expiration timestamp for options/futures contracts
  * `min_price_increment` \- The minimum constant trade tick for an instrument

## Historical
example

First, we'll take a look at how to do this with the Historical client.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # Create a historical client
    client = db.Historical("$YOUR_API_KEY")
    
    dataset = "EQUS.SUMMARY"
    
    # Download definition schema for all symbols
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols="ALL_SYMBOLS",
        start="2025-02-06",
        end="2025-02-07",
        schema="definition",
    )
    
    # Create a map of raw_symbol -> InstrumentDefMsgs
    symbol_map = {msg.raw_symbol: msg for msg in data}
    
    # Get all symbols
    symbols = sorted(symbol_map.keys())
    
    # Print out the symbol count and the first 5 symbols
    print(f"Total symbol count for {dataset} = {len(symbols)}\nFirst 5 symbols...{symbols[:5]}")
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    Total symbol count for EQUS.SUMMARY = 11198
    First 5 symbols...['A', 'AA', 'AAA', 'AAAU', 'AACBU']
    

## Live
example

Next, we'll take a look at how to do this with the Live client.

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> You will need a live license for the dataset to run this example. See our
> [plans and live data page](https://databento.com/docs/portal/live-data) for
> more information.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # Create a live client
    live_client = db.Live("$YOUR_API_KEY")
    
    dataset = "GLBX.MDP3"
    
    # Subscribe to the definition schema for all symbols for this session
    live_client.subscribe(
        dataset=dataset,
        schema="definition",
        symbols="ALL_SYMBOLS",
        start=0,
    )
    
    # Create a map of raw_symbol -> InstrumentDefMsgs
    symbol_map = {}
    def append_symbol_map(msg):
        if isinstance(msg, db.InstrumentDefMsg):
            symbol_map[msg.raw_symbol] = msg
    
    # Add the callback and start the stream
    live_client.add_callback(append_symbol_map)
    live_client.start()
    
    # Listen to the stream for 10 seconds to get all messages
    live_client.block_for_close(timeout=10)
    
    # Get all symbols
    symbols = sorted(symbol_map.keys())
    
    # Print out the symbol count and the first 5 symbols
    print(f"Total symbol count for {dataset} = {len(symbols)}\nFirst 5 symbols...{symbols[:5]}")
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    Total symbol count for GLBX.MDP3 = 784038
    First 5 symbols...['00CH5', '00CJ5', '00CK5', '00CM5', '00CN5']
    

