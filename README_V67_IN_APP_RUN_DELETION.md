# Railway Worker v67 — In-App Run Deletion + Disk Usage

Adds endpoints so runs can be deleted from the UI to reclaim volume space,
instead of using the Railway console.

New endpoints:
- POST /repository/runs/delete        {password, confirm:true, run_id}
- POST /repository/runs/delete-many   {password, confirm:true, run_ids:[...]}
- GET  /repository/runs/disk-usage    -> volume total/used/free/pct + runs MB

Safety:
- Refuses to delete a run whose process/thread is still live.
- Refuses _jobs, undo_redo, workspaces, lost+found, the nested `runs` folder,
  and all loose top-level files (global_scan_history.csv, dashboard_data.json).
- Only deletes directories strictly inside RUNS_DIR with a recognised run
  prefix (run_/recheck_/presence_/discovery/story/enrich/repair/pre_count_rebuild).
- Requires admin password AND explicit confirm flag.

Pairs with the frontend delete button + disk badge (v131).
