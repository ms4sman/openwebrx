from owrx.config import Config
from urllib import request
import json
from datetime import datetime, timedelta
import logging
import threading
from owrx.map import Map, LatLngLocation
from owrx.parser import Parser

logger = logging.getLogger(__name__)


class DmrCache(object):
    sharedInstance = None

    @staticmethod
    def getSharedInstance():
        if DmrCache.sharedInstance is None:
            DmrCache.sharedInstance = DmrCache()
        return DmrCache.sharedInstance

    def __init__(self):
        self.cache = {}
        self.cacheTimeout = timedelta(seconds=86400)

    def isValid(self, key):
        if key not in self.cache:
            return False
        entry = self.cache[key]
        return entry["timestamp"] + self.cacheTimeout > datetime.now()

    def put(self, key, value):
        self.cache[key] = {"timestamp": datetime.now(), "data": value}

    def get(self, key):
        if not self.isValid(key):
            return None
        return self.cache[key]["data"]


class DmrMetaEnricher(object):
    def __init__(self):
        self.threads = {}

    def downloadRadioIdData(self, id):
        cache = DmrCache.getSharedInstance()
        try:
            logger.debug("requesting DMR metadata for id=%s", id)
            res = request.urlopen("https://www.radioid.net/api/dmr/user/?id={0}".format(id), timeout=30).read()
            data = json.loads(res.decode("utf-8"))
            cache.put(id, data)
        except json.JSONDecodeError:
            cache.put(id, None)
        del self.threads[id]

    def enrich(self, meta):
        if not Config.get()["digital_voice_dmr_id_lookup"]:
            return meta
        if "source" not in meta:
            return meta
        id = meta["source"]
        cache = DmrCache.getSharedInstance()
        if not cache.isValid(id):
            if id not in self.threads:
                self.threads[id] = threading.Thread(target=self.downloadRadioIdData, args=[id], daemon=True)
                self.threads[id].start()
            return meta
        data = cache.get(id)
        if data is not None and "count" in data and data["count"] > 0 and "results" in data:
            meta["additional"] = data["results"][0]
        return meta


class YsfMetaEnricher(object):
    def __init__(self, parser):
        self.parser = parser

    def enrich(self, meta):
        for key in ["source", "up", "down", "target"]:
            if key in meta:
                meta[key] = meta[key].strip()
        for key in ["lat", "lon"]:
            if key in meta:
                meta[key] = float(meta[key])
        if "source" in meta and "lat" in meta and "lon" in meta:
            # TODO parsing the float values should probably happen earlier
            loc = LatLngLocation(meta["lat"], meta["lon"])
            Map.getSharedInstance().updateLocation(meta["source"], loc, "YSF", self.parser.getBand())
        return meta


class MetaParser(Parser):
    def __init__(self, handler):
        super().__init__(handler)
        self.enrichers = {"DMR": DmrMetaEnricher(), "YSF": YsfMetaEnricher(self)}

    def parse(self, meta):
        fields = meta.split(";")
        meta = {v[0]: "".join(v[1:]) for v in map(lambda x: x.split(":"), fields) if v[0] != ""}

        if "protocol" in meta:
            protocol = meta["protocol"]
            if protocol in self.enrichers:
                meta = self.enrichers[protocol].enrich(meta)
        self.handler.write_metadata(meta)
