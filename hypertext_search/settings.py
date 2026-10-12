BOT_NAME = "hypertext-search"
LOG_LEVEL = "INFO"
SPIDER_MODULES = ["hypertext_search.spiders"]
NEWSPIDER_MODULE = "hypertext_search.spiders"
USER_AGENT = "HypertextSearch/0.2 (+https://github.com/annihalated/hypertext-search)"
ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 50
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 0.1
RANDOMIZE_DOWNLOAD_DELAY = False
DOWNLOAD_TIMEOUT = 30
DNS_TIMEOUT = 10
DOWNLOAD_MAXSIZE = 10 * 1024 * 1024
DOWNLOAD_WARNSIZE = 5 * 1024 * 1024
REDIRECT_MAX_TIMES = 5
RETRY_TIMES = 2
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False
JOBDIR = "data/crawls/text-1"
DOWNLOAD_SLOTS = {"substack": {"concurrency": 1, "delay": 1.0, "randomize_delay": False}}
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoSQLiteQueue"
SCHEDULER_START_DISK_QUEUE = "scrapy.squeues.PickleFifoSQLiteQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"
SCHEDULER_DEBUG = True
DNS_RESOLVER = "hypertext_search.middlewares.PublicResolver"
DOWNLOADER_CLIENTCONTEXTFACTORY = "scrapy.core.downloader.contextfactory.BrowserLikeContextFactory"
REQUEST_FINGERPRINTER_CLASS = "hypertext_search.middlewares.ScopeFingerprinter"
DOWNLOADER_MIDDLEWARES = {
    "hypertext_search.middlewares.PlatformSlotMiddleware": 50,
    "hypertext_search.middlewares.ScopeMiddleware": 60,
    "hypertext_search.middlewares.RetryAfterMiddleware": 560,
}
EXTENSIONS = {"hypertext_search.extensions.DiscoveryAudit": 500}
ITEM_PIPELINES = {"hypertext_search.pipelines.ObservationPipeline": 300}
