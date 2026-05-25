FAQs

# Streaming vs.
batch download

We support both **streaming** and **batch download** as means of receiving
historical data. Both methods yield exactly the same data. They differ only in
the way that the data is received. The table below compares the two methods.

| Streaming | Batch download  
---|---|---  
Usage | Load data directly in your application via our [API or client libraries](/docs/faqs/differences-our-client-libraries-apis). | Download flat files from the [Download center](/docs/portal/download-center). HTTP and FTP are supported.  
Cost | You will be charged every time you stream the same data. | Download the same data multiple times over a 30 day period, for no additional charge.  
Size | Recommended for data requests under 5 GB. | Recommended for data requests over 5 GB.  
Customization | Limited | Full set of advanced customizations available.  
Wait time | None | Usually takes several minutes to prepare before the data is available in the Download center.  
Request method | [API](/docs/api-reference-historical/timeseries/timeseries-get-range) only | Either manually using a [Batch download request](/docs/portal/batch-download) or programmatically via our [API](/docs/api-reference-historical/batch/batch-submit-job).  
  
Here are some details to consider when deciding which method is best for you:

  * **Usage.** With streaming, you can access data immediately when it is needed in your application. Streaming is suitable for smaller, on-demand workflows such as retrieving reference data for production trading, initializing a user's price chart for a given symbol on a display application, or exploring data in an interactive environment.
  * **Cost.** Since streaming is designed for one-off tasks, you will be charged each time for duplicate stream requests. If you intend to access the same data repeatedly -- for instance in parallel simulations, multiple backtests or daily ETL pipelines -- you can do so more efficiently with a batch download of the data onto your system.
  * **Size.** Batch download lets you fetch the same data multiple times without additional charge. Hence, it is more suitable for larger data requests where there is risk of disconnection while your data is being transmitted.
  * **Customization** and **wait time.** Our streaming infrastructure is optimized for instant retrieval of small data requests in [predetermined schemas](/docs/schemas-and-data-formats). When you request a batch download, our system needs time to prepare the data files. This preparation time lets us service additional customizations that are not possible on our streaming infrastructure.
  * **Request method.** A batch download request can be submitted using the [Batch download](https://databento.com) on our portal, using our HTTP API, or using any of our official client libraries.

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> Both methods are suitable for **market replay**. All of our historical data
> is sequenced in the order of arrival on our live service and includes
> receive timestamps. This enables you to replay events in the exact order as
> they would've been received under live market conditions.

