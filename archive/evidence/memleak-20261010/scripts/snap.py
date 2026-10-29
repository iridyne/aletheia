# read-only heap snapshot, injected via sys.remote_exec (PEP 768)
import gc, sys, os, time, json, threading, traceback, collections, tracemalloc
OUT = "/home/admin/aletheia-leak-20261010"
tag = time.strftime("%H%M%S")
def rss():
    d = {}
    for l in open("/proc/self/status"):
        if l.split(":")[0] in ("VmRSS", "RssAnon", "RssFile", "VmSwap"): d[l.split(":")[0]] = int(l.split()[1]) // 1024
    return d
r = {"tag": tag, "time": time.strftime("%F %T"), "pid": os.getpid(), "mem_mb": rss(),
     "tracemalloc_tracing": tracemalloc.is_tracing(), "gc_counts": gc.get_count(), "gc_stats": gc.get_stats(),
     "gc_threshold": gc.get_threshold(), "gc_freeze_count": gc.get_freeze_count()}
try:
    t0 = time.time(); objs = gc.get_objects(); r["n_gc_objects"] = len(objs)
    cnt = collections.Counter(); sz = collections.Counter()
    for o in objs:
        k = str(getattr(type(o), "__module__", "?")) + "." + str(getattr(type(o), "__qualname__", "?"))
        cnt[k] += 1
        try: sz[k] += sys.getsizeof(o)
        except Exception: pass
    r["top_by_count"] = cnt.most_common(40)
    r["top_by_shallow_bytes_mb"] = [(k, round(v / 1048576, 1)) for k, v in sz.most_common(40)]
    # big containers: who owns them
    big = []
    for o in objs:
        if isinstance(o, (dict, list, set, collections.deque)) and len(o) > 20000:
            big.append((type(o).__name__, len(o), sys.getsizeof(o) // 1024, repr(o)[:160] if len(o) else ""))
    r["big_containers"] = sorted(big, key=lambda x: -x[1])[:25]
    # module-level globals that are big containers
    mg = []
    for name, m in list(sys.modules.items()):
        d = getattr(m, "__dict__", {})
        for k, v in list(d.items()):
            if isinstance(v, (dict, list, set, collections.deque)) and len(v) > 5000:
                mg.append((name + "." + k, type(v).__name__, len(v)))
    r["big_module_globals"] = sorted(mg, key=lambda x: -x[2])[:30]
    r["scan_secs"] = round(time.time() - t0, 1)
    del objs
except Exception: r["scan_error"] = traceback.format_exc()
r["threads"] = [(t.name, t.daemon) for t in threading.enumerate()]
r["n_modules"] = len(sys.modules)
r["heavy_libs_loaded"] = [m for m in ("torch","numpy","pandas","onnxruntime","sentence_transformers","transformers","faiss","sklearn","scipy","pyarrow","polars","duckdb","lxml","PIL","cv2","sqlalchemy","aiohttp","httpx","playwright","selenium","talib","tensorflow","jax","lightgbm","xgboost") if m in sys.modules]
try:
    import ctypes
    class MI(ctypes.Structure): _fields_=[(n,ctypes.c_size_t) for n in "arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost".split()]
    libc=ctypes.CDLL("libc.so.6"); libc.mallinfo2.restype=MI; mi=libc.mallinfo2()
    r["mallinfo_mb"]={n:round(getattr(mi,n)/1048576,1) for n,_ in MI._fields_}
except Exception as e: r["mallinfo_error"]=repr(e)
r["pymalloc_allocated_blocks"]=sys.getallocatedblocks()
if tracemalloc.is_tracing(): r["tracemalloc_traced_mb"]=[round(x/1048576,1) for x in tracemalloc.get_traced_memory()]
stacks = []
for tid, fr in sys._current_frames().items():
    stacks.append("".join(traceback.format_stack(fr)[-8:]))
r["stacks"] = stacks
r["asyncio_tasks"] = None
try:
    import asyncio
    n = collections.Counter()
    for o in gc.get_objects():
        if isinstance(o, asyncio.Task):
            c = o.get_coro(); n[getattr(c, "__qualname__", repr(c))] += 1
    r["asyncio_tasks"] = n.most_common(25)
except Exception: pass
json.dump(r, open(f"{OUT}/snap_{tag}.json", "w"), indent=1, default=str)

if not tracemalloc.is_tracing():
    tracemalloc.start(12)
    open(OUT + "/tracemalloc_started_" + tag, "w").write(str(time.time()))
