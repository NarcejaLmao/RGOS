def load_conf():
    conf = dict(DEFAULTS)
    for path in CONF_FILES:
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                conf.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    # User overrides survive updates, but malformed values must not break card
    # discovery or accidentally target the primary ROMS mount.
    if not isinstance(conf.get("enabled"), bool):
        conf["enabled"] = DEFAULTS["enabled"]
    if not isinstance(conf.get("readonly"), bool):
        conf["readonly"] = DEFAULTS["readonly"]
    if not isinstance(conf.get("create_folders"), bool):
        conf["create_folders"] = DEFAULTS["create_folders"]

    fs = conf.get("fs")
    allowed_fs = tuple(MOUNT_TYPES)
    if not isinstance(fs, list):
        fs = list(DEFAULTS["fs"])
    conf["fs"] = [item for item in fs if isinstance(item, str) and item in allowed_fs]
    if not conf["fs"]:
        conf["fs"] = list(DEFAULTS["fs"])

    min_mb = conf.get("min_mb")
    if isinstance(min_mb, bool) or not isinstance(min_mb, int) or not 1 <= min_mb <= 1024:
        conf["min_mb"] = DEFAULTS["min_mb"]

    mp = conf.get("mountpoint")
    if not isinstance(mp, str) or not mp.startswith("/media/"):
        mp = DEFAULTS["mountpoint"]
    else:
        mp = os.path.normpath(mp)
        # Never let a config override mount over the primary card or /media itself.
        if mp in ("/media", PRIMARY_MOUNT) or mp.startswith(PRIMARY_MOUNT + "/"):
            mp = DEFAULTS["mountpoint"]
    conf["mountpoint"] = mp
    return conf
