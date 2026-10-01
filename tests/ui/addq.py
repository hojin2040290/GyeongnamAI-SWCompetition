import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("insert into agentquestion(user_id,job_id,event,run_id,question,options_json,why,law,context_json,status,answer,created_at) "
          "values(1,?,'contract_check','x','서류를 냈나요?','[\"냈어요\", \"안 냈어요\"]','','','{}','open','',datetime('now'))", (int(sys.argv[2]),))
c.commit()
