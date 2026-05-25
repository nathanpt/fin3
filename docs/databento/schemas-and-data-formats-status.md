Schemas and data formats

# ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Status![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

**Status** provides updates about the trading session, such as halts, pauses,
short-selling restrictions, auction start, and other matching engine statuses.
The granularity and frequency of these updates vary by publisher and dataset.

## Fields
(`status`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_recv](/docs/standards-and-conventions/common-fields-enums-types#ts-recv).  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 18 in the status schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`action` | uint16_t | The type of status change. See [Status actions](/docs/schemas-and-data-formats/status#status-action-variants) table below.  
`reason` | uint16_t | Additional details about the cause of the status change. See [Status reasons](/docs/schemas-and-data-formats/status#status-reason-variants) table below.  
`trading_event` | uint16_t | Further information about the status change (if provided). See [Trading events](/docs/schemas-and-data-formats/status#trading-event-variants) table below.  
`is_trading` | char | The best-efforts state of trading in the instrument, either `Y`, `N`, or `~`.  
`is_quoting` | char | The best-efforts state of quoting in the instrument, either `Y`, `N`, or `~`.  
`is_short_sell_restricted` | char | The best-efforts state of short sell restrictions for the instrument (if applicable), either `Y`, `N`, or `~`.  
  
## Status action
variants

Variant | `action` | Description  
---|---|---  
None | `0` | No change.  
Pre-open | `1` | The instrument is in a pre-open period.  
Pre-cross | `2` | The instrument is in a pre-cross period.  
Quoting | `3` | The instrument is quoting but not trading.  
Cross | `4` | The instrument is in a cross/auction.  
Rotation | `5` | The instrument is being opened through a trading rotation.  
New price indication | `6` | A new price indication is available for the instrument.  
Trading | `7` | The instrument is trading.  
Halt | `8` | Trading in the instrument has been halted.  
Pause | `9` | Trading in the instrument has been paused.  
Suspend | `10` | Trading in the instrument has been suspended.  
Pre-close | `11` | The instrument is in a pre-close period.  
Close | `12` | Trading in the instrument has closed.  
Post-close | `13` | The instrument is in a post-close period.  
Short sell restriction (SSR) change | `14` | A change in short-selling restrictions.  
Not available for trading | `15` | The instrument is not available for trading, either trading has closed or been halted.  
  
## Status reason
variants

Variant | `reason` | Description  
---|---|---  
None | `0` | No reason given.  
Scheduled | `1` | The change in status occurred as scheduled.  
Surveillance intervention | `2` | The instrument stopped due to a market surveillance intervention.  
Market event | `3` | The status changed due to activity in the market.  
Instrument activation | `4` | The derivative instrument began trading.  
Instrument expiration | `5` | The derivative instrument expired.  
Recovery in process | `6` | Recovery in progress.  
Regulatory | `10` | The status change was caused by a regulatory action.  
Administrative | `11` | The status change was caused by an administrative action.  
Non-compliance | `12` | The status change was caused by the issuer not being compliance with regulatory requirements.  
Filings not current | `13` | Trading halted because the issuer's filings are not current.  
SEC trading suspension | `14` | Trading halted due to an SEC trading suspension.  
New issue | `15` | The status changed because a new issue is available.  
Issue available | `16` | The status changed because an issue is available.  
Issues reviewed | `17` | The status changed because the issue(s) were reviewed.  
Filing requirements satisfied | `18` | The status changed because the filing requirements were satisfied.  
News pending | `30` | Relevant news is pending.  
News released | `31` | Relevant news was released.  
News and resumption times | `32` | The news has been fully disseminated and times are available for the resumption in quoting and trading.  
News not forthcoming | `33` | The relevant news was not forthcoming.  
Order imbalance | `40` | Halted for order imbalance.  
LULD pause | `50` | The instrument hit limit up or limit down.  
Operational | `60` | An operational issue occurred with the venue.  
Additional information requested | `70` | The status changed until the exchange receives additional information.  
Merger effective | `80` | Trading halted due to merger becoming effective.  
ETF | `90` | Trading is halted in an ETF due to conditions with the component securities.  
Corporate action | `100` | Trading is halted for a corporate action.  
New Security offering | `110` | Trading is halted because the instrument is a new offering.  
Market wide halt level 1 | `120` | Halted due to the market-wide circuit breaker level 1.  
Market wide halt level 2 | `121` | Halted due to the market-wide circuit breaker level 2.  
Market wide halt level 3 | `122` | Halted due to the market-wide circuit breaker level 3.  
Market wide halt carryover | `123` | Halted due to the carryover of a market-wide circuit breaker from the previous trading day.  
Market wide halt resumption | `124` | Resumption due to the end of a market-wide circuit breaker halt.  
Quotation not available | `130` | Halted because quotation is not available.  
  
## Trading event
variants

Variant | `trading_event` | Description  
---|---|---  
None | `0` | No additional information given.  
No cancel | `1` | Order entry is allowed. Modification and cancellation are not allowed.  
Change trading session | `2` | A change of trading session occurred. Daily statistics are reset.  
Implied matching on | `3` | Implied matching is available.  
Implied matching off | `4` | Implied matching is not available.  
  
## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Snapshots![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

As trading status can carry across UTC days, the historical API includes a
snapshot of the last status record for each active instrument at UTC midnight.

