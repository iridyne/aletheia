import tracemalloc, json, time, linecache
OUT="/home/admin/aletheia-leak-20261010"
s=tracemalloc.take_snapshot().filter_traces((tracemalloc.Filter(False,"<frozen importlib._bootstrap>"),tracemalloc.Filter(False,"<frozen importlib._bootstrap_external>")))
r={"time":time.strftime("%F %T"),"traced_cur_peak_mb":[round(x/1048576,1) for x in tracemalloc.get_traced_memory()]}
r["top_lineno"]=[(str(x.traceback[0]),round(x.size/1048576,2),x.count) for x in s.statistics("lineno")[:30]]
r["top_traceback"]=[(round(x.size/1048576,2),x.count,[str(f) for f in x.traceback]) for x in s.statistics("traceback")[:8]]
json.dump(r,open(OUT+"/tracemalloc_top.json","w"),indent=1)
tracemalloc.stop()
