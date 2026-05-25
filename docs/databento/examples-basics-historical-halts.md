Examples and tutorials

Historical data

# Market halts,
volatility interrupts, and price bands![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Venues implement different types of market integrity controls to ensure a fair
and efficient market. Some of these controls will limit how far price can move
in one session, as well as how much price can move in a short period of time.
When these events are triggered, it can result in a temporary halt in trading
or quoting.

## Velocity
Logic

CME Globex has [Velocity Logic](https://www.cmegroup.com/education/demos-and-
tutorials/understanding-velocity-logic.html), which prevents price from moving
too far, too fast. During Velocity Logic, an instrument will transition to a
"Pre-Open" state. While this state is normally seen before the full session
open for CME Globex, it is also seen during these events. At the end of the
event, the instrument will transition back to an "Open" state.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

On 2025-04-09, a tariff pause announcement was made that led to significantly
increased volatility on CME Globex. Multiple Velocity Logic events were
triggered in Equity Index futures following the announcement.

In this example, we'll take a look at just one of the Velocity Logic events
after the announcement. We'll use the [status schema](/docs/schemas-and-data-
formats/status) to show the changes in market state during this event. Next,
we'll plot the BBO and trades from the [MBP-1 schema](/docs/schemas-and-data-
formats/mbp-1) to show how quoting and trading were affected. Additionally,
we'll use the [statistics schema](/docs/schemas-and-data-formats/statistics)
to show some exchange generated statistics.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    import matplotlib.pyplot as plt
    
    # Create historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Set parameters
    dataset = "GLBX.MDP3"
    product = "NQ"
    start = "2025-04-09T17:18:40"
    end = "2025-04-09T17:18:55"
    
    # Request status data and convert to DataFrame
    status_data = client.timeseries.get_range(
        dataset=dataset,
        schema="status",
        start=start,
        end=end,
        symbols=f"{product}.v.0",
        stype_in="continuous",
    )
    status_df = status_data.to_df()
    
    status_df = status_df[status_df["action"].isin([db.StatusAction.PRE_OPEN, db.StatusAction.TRADING])]
    status_df["action"] = status_df["action"].map({
        db.StatusAction.PRE_OPEN: "Pre-Open",
        db.StatusAction.TRADING: "Open",
    })
    
    def add_status_vlines(ax):
        for status_change in status_df.iterrows():
            ax.axvline(status_change[0], color="dimgray", linestyle=":", linewidth=1.5)
            ax.text(
                status_change[0],
                ax.get_ylim()[1],
                status_change[1]["action"],
                color="dimgray",
                ha="center",
            )
    
    # Request MBP-1 data and convert to DataFrame
    mbp1_data = client.timeseries.get_range(
        dataset=dataset,
        schema="mbp-1",
        start=start,
        end=end,
        symbols=f"{product}.v.0",
        stype_in="continuous",
    )
    mbp1_df = mbp1_data.to_df()
    mbp1_df = mbp1_df.rename(columns={"bid_px_00": "Bid", "ask_px_00": "Ask"})
    
    # Request statistics data and convert to DataFrame
    stats_data = client.timeseries.get_range(
        dataset=dataset,
        schema="statistics",
        start=start,
        end=end,
        symbols=f"{product}.v.0",
        stype_in="continuous",
    )
    stats_df = stats_data.to_df()
    stats_df = stats_df[stats_df["stat_type"] == db.StatType.INDICATIVE_OPENING_PRICE]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10.9, 10.9), sharey=True)
    
    # Plot trades
    trades_df = mbp1_df[mbp1_df["action"] == db.Action.TRADE]
    trades_df["price"].plot(
        ax=ax1,
        style=".",
        markersize=5,
        color="C4",
        xlabel="Time (UTC)",
        ylabel="Price",
        label="Trades",
        title=f"{product} Trades",
    )
    add_status_vlines(ax1)
    ax1.legend()
    
    # Plot top of book data with Indicative Opening Price
    mbp1_df[["Bid", "Ask"]].plot(ax=ax2, drawstyle="steps-post")
    add_status_vlines(ax2)
    
    stats_df["price"].plot(
        ax=ax2,
        style=".",
        markersize=10,
        color="C2",
        xlabel="Time (UTC)",
        ylabel="Price",
        label="IOP",
        title=f"{product} Top of Book",
    )
    ax2.legend()
    
    plt.tight_layout()
    plt.show()
    

In this first plot, you'll see how trading activity will temporarily halt when
the market transitions to a Pre-Open state during Velocity Logic.

The second plot takes a look at the BBO. While quoting is still allowed during
these events, the bid-ask spread can become crossed.

During these Pre-Open states, the exchange publishes a statistic called the
[Indicative Opening Price](https://www.cmegroup.com/education/indicative-
opening-price-overview.html), also known as the "IOP". This statistic, which
can be updated multiple times during a Pre-Open state, provides the most
probable price that trading will resume at when the market transitions back to
an Open state.

When the market transitions back to an Open state, the bid-ask spread will
uncross, and trading will resume.

![Velocity Logic 1](/docs/05-05-10-halts-0.svg)

