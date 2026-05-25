### Dates and
times

Our Python client library has several functions with timestamp arguments. These arguments will have type `pandas.Timestamp | datetime.date | str | int` and support a variety of formats.

It's recommended to use [pandas.Timestamp](https://pandas.pydata.org/pandas-
docs/stable/reference/api/pandas.Timestamp.html), which fully supports
timezones and nanosecond-precision. If a `datetime.date` is used, the time is
set to midnight UTC. If an `int` is provided, the value is interpreted as
[UNIX nanoseconds](https://en.wikipedia.org/wiki/Unix_time).

The client library also handles several string-based timestamp formats based
on [ISO 8601](https://www.iso.org/iso-8601-date-and-time-format.html).

  * `yyyy-mm-dd`, e.g. `"2022-02-28"` (midnight UTC)
  * `yyyy-mm-ddTHH:MM`, e.g. `"2022-02-28T23:50"`
  * `yyyy-mm-ddTHH:MM:SS`, e.g. `"2022-02-28T23:50:59"`
  * `yyyy-mm-ddTHH:MM:SS.NNNNNNNNN`, e.g. `"2022-02-28T23:50:59.123456789"`

Timezone specification is also supported.

  * `yyyy-mm-ddTHH:MMZ`
  * `yyyy-mm-ddTHH:MM±hh`
  * `yyyy-mm-ddTHH:MM±hhmm`
  * `yyyy-mm-ddTHH:MM±hh:mm`

**Bare dates**

Some parameters require a bare date, without a time. These arguments have type `datetime.date | str` and must either be a `datetime.date` object, or a string in `yyyy-mm-dd` format, e.g. `"2022-02-28"`.

