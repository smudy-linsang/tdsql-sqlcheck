const API_BASE = '';
const { createApp, ref, reactive, onMounted, watch, nextTick, computed } = Vue;
const AUTH_TOKEN_KEY = 'tdsql_token';
function getToken(){return localStorage.getItem(AUTH_TOKEN_KEY)||''}
function setToken(t){localStorage.setItem(AUTH_TOKEN_KEY,t)}
function clearToken(){localStorage.removeItem(AUTH_TOKEN_KEY)}
let onUnauthorized=null;
async function apiFetch(url,options={}){
  const opts=Object.assign({},options);
  opts.headers=Object.assign({},options.headers||{});
  const token=getToken();
  if(token)opts.headers['Authorization']='Bearer '+token;
  let finalUrl=url;
  if(!opts.method||opts.method.toUpperCase()==='GET'){
    const sep=url.includes('?')?'&':'?';
    finalUrl=`${url}${sep}_t=${Date.now()}`;
  }
  const resp=await fetch(finalUrl,opts);
  if(resp.status===401&&onUnauthorized){clearToken();onUnauthorized()}
  else if(resp.status>=500){try{const d=await resp.clone().json();ElementPlus.ElNotification.error({title:'服务异常',message:d.detail||'服务暂时不可用，请稍后重试'})}catch(e){ElementPlus.ElNotification.error({title:'服务异常',message:'服务暂时不可用，请稍后重试'})}}
  return resp;
}
const app=createApp({
  setup(){
    const currentPage=ref('dashboard');
    const sidebarCollapsed=ref(false);
    // 主题：深/浅双风格，localStorage 记忆；仅切换 <html data-theme> + 重绘图表，无逻辑/DOM 改动
    const theme=ref((()=>{try{const t=localStorage.getItem('tdsql-theme');return (t==='light'||t==='dark')?t:'dark'}catch(e){return 'dark'}})());
    const applyTheme=()=>{document.documentElement.setAttribute('data-theme',theme.value);try{localStorage.setItem('tdsql-theme',theme.value)}catch(e){}};
    const isDarkTheme=()=>document.documentElement.getAttribute('data-theme')!=='light';
    const toggleTheme=()=>{theme.value=theme.value==='dark'?'light':'dark';applyTheme();nextTick(()=>{try{if(currentPage.value==='dashboard')renderTrendChart();if(dailyTrendChartRef.value)renderDailyTrendChart()}catch(e){}})};
    applyTheme();
    const authState=reactive({token:getToken(),user:null,role:''});
    const loginForm=reactive({username:'',password:''});
    const loginLoading=ref(false);
    const loginError=ref('');
    const pwdDialog=reactive({visible:false,old_password:'',new_password:'',loading:false,forced:false});
    const savedConnections=ref([]);
    const currentConnectionId=ref(localStorage.getItem('tdsql_conn')||'');
    const projects=ref([]);
    const currentProjectId=ref('');
    const activeAlerts=ref(0);
    const metadataEnhanced=ref(false);
    const statsLoading=ref(false);
    const stats=ref({audit:{},slow_queries:{},rules:{},recent_audits:[]});
    const ruleHits=ref([]);
    const trendChartRef=ref(null);
    const sqlInput=ref('');
    const auditing=ref(false);
    const auditResult=ref(null);
    const auditProjectId=ref('');
    const fileAuditTab=ref('upload');
    const fileAuditResult=ref(null);
    const fileReports=ref([]);
    const fileReportsLoading=ref(false);
    const fileReportsTotal=ref(0);
    const fileReportsPage=ref(1);
    const rulesList=ref([]);
    const rulesByCategory=ref({});
    const ruleSearch=ref('');
    const expandedCategories=ref([]);
    const slowList=ref([]);
    const slowListLoading=ref(false);
    const slowFilters=reactive({db_name:'',set_id:'',severity:'',status:'',keyword:'',scan_task_id:'',created_by:''});
    const slowPage=reactive({current:1,size:20,total:0});
    const scanTasks=ref([]);
    const scanTaskTotal=ref(0);
    const scanTaskCurrentPage=ref(1);
    const scanTaskLoading=ref(false);
    const selectedTaskIds=ref(new Set());
    const batchDeleting=ref(false);
    const clearingOrphan=ref(false);
    const scanDrawer=ref(false);
    const scanTimeWindow=ref([]);
    const scanTaskForm=reactive({connection_id:'',task_name:'',source:'monitordb',min_time:0.1,limit:50,poll_duration:10,poll_interval:1});
    const slowDetailDrawer=ref(false);
    const slowDetail=ref(null);
    const explainMode=ref('sql');
    const explainSqlInput=ref('');
    const explainInput=ref('');
    const explainConnId=ref('');
    const analyzingExplain=ref(false);
    const explainResult=ref(null);
    const tdsqlStatus=ref({connected:false});
    const connDrawer=ref(false);
    const connForm=reactive({id:'',name:'',host:'',port:3306,username:'',password:'',database:'',is_distributed:true,description:'',set_list:'',monitor_host:'',monitor_port:15001,monitor_user:'',monitor_password:'',monitor_db:'tdsqlpcloud_monitor'});
    const connEditMode=ref(false);
    const connTestResult=ref(null);
    const connTesting=ref(false);
    const connLoading=ref(false);
    const usersList=ref([]);
    const usersLoading=ref(false);
    const userDialog=reactive({visible:false,loading:false,form:{username:'',display_name:'',role:'developer',password:''}});
    const resetDialog=reactive({visible:false,username:'',password:''});
    // 新页面状态
    const scanSchedules=ref([]);
    const scanScheduleLoading=ref(false);
    const scheduleDrawer=ref(false);
    const scheduleForm=reactive({connection_id:'',source:'digest',cron_hour:2,cron_minute:0,limit_rows:50,min_time:0.1,enabled:true,task_name:''});
    const healthLoading=ref(false);
    const healthResult=ref(null);
    const healthCheckType=ref('charset');
    const healthDbName=ref('');
    const schemaCheckConnId=ref('');
    const schemaCheckScope=ref(['TABLE','INDEX','VIEW','SHARDKEY']);
    const schemaCheckResults=ref([]);
    const schemaCheckSummary=ref({total:0,error:0,warning:0,info:0,checks_passed:0,checks_failed:0});
    // 在线元数据提取与文件审核
    const extractedAuditConnId=ref('');
    const extractedDbName=ref('');
    const extractedScope=ref(['TABLE','INDEX','VIEW','SHARDKEY']);
    const extractAuditing=ref(false);
    const extractedResult=ref({});
    const extractedTab=ref('audit');
    const extractedReports=ref([]);
    const extractedReportsLoading=ref(false);
    // 深度诊断（G3/G5/G6/G7/G8）
    const deepConnId=ref('');
    const deepRightConnId=ref('');
    const deepDb=ref('');
    const deepTab=ref('cluster');
    const deepLoading=ref('');
    const deepResult=reactive({cluster:null,index:null,diff:null,emergency:null,sqlstats:null});
    // G10-G13 新增状态
    const zkDialogVisible=ref(false);
    const zkForm=reactive({zk_server:'127.0.0.1:2118',zk_auth_user:'tdsqlsys_zk',zk_auth_password:'',zk_root:'/tdsqlzk',zkcli_path:'/data/application/zookeeper/bin/zkCli.sh',proxy_mode:'random',default_database:'ALL',force_mock:false});
    const zkScanning=ref(false);
    const zkDiscovered=ref([]);
    const zkSelected=ref([]);
    const zkRegistering=ref(false);
    const gatewayLoading=ref(false);
    const gatewayReports=ref([]);
    const gatewayHtml=ref('');
    const gatewayDetailVisible=ref(false);
    const pptLoading=ref(false);
    const pptDashboard=ref(null);
    const toolkitLoading=ref(false);
    const toolkitScripts=ref([]);
    const schemaCheckLoading=ref(false);
    const bigtableLoading=ref(false);
    // G4: 每日巡检与对比报告新增状态
    const dailyInspectDates=ref([]);
    const dailyInspectThreshold=ref(1.0);
    const dailyCompareResult=ref(null);
    const dailyInstSearch=ref('');
    const dailyInstSigOnly=ref(false);
    const dailySrvSearch=ref('');
    const dailySrvSigOnly=ref(false);
    const dailyInspectChartData=ref(null);
    const dailyInspectChartMetric=ref('cpu_peak');
    const dailyInspectChartNode=ref('');
    const dailyInspectChartNodes=ref([]);
    const dailyTrendChartRef=ref(null);
    const bigtableData=ref(null);
    const bigtableCollecting=ref(false);
    const bigtableRef=ref(null);
    const partitionDetail=reactive({});
    const partitionLoading=reactive({});
    const projectsList=ref([]);
    const projectsLoading=ref(false);
    const projectDialog=reactive({visible:false,loading:false,form:{project_name:'',tdsql_connection_id:'',rule_set_id:'default',gate_rule_id:'default',gitlab_url:'',description:''}});
    const rulesets=ref([]);
    const rulesetsLoading=ref(false);
    const rulesetDialog=reactive({visible:false,loading:false,form:{id:'',name:'',description:''}});
    const rulesetDrawer=reactive({visible:false,loading:false,saving:false,ruleset:null,searchQuery:'',categoryFilter:'ALL'});
    const rulesetConfigItems=ref([]);
    const gateRules=ref(null);
    const gateStrategies=ref([]);
    const gateLoading=ref(false);
    const gateCustom=reactive({visible:false,max_error_count:0,max_warning_count:10});
    const monitorAlerts=ref([]);
    const monitorRules=ref([]);
    const monitorLoading=ref(false);
    const monitorTab=ref('alerts');
    const monitorRuleDialog=reactive({visible:false,loading:false,form:{metric_name:'',warning_threshold:0,urgent_threshold:0,check_interval_sec:60,enabled:true}});
    const inspectionTasks=ref([]);
    const inspectionLoading=ref(false);
    const inspectionDialog=reactive({visible:false,loading:false,form:{connection_id:'',inspection_type:'full'}});
    const inspectionResultDrawer=ref(false);
    const inspectionResults=ref([]);
    const auditLogs=ref([]);
    const auditLogsLoading=ref(false);
    const auditLogsTotal=ref(0);
    const auditLogsPage=ref(1);
    const retentionPolicies=ref([]);
    const retentionLoading=ref(false);
    const retentionDialog=reactive({visible:false,loading:false,form:{table_name:'',retention_days:30,enabled:true}});
    const retentionEditMode=ref(false);
    const sysInfo=ref(null);
    const sysInfoLoading=ref(false);
    // V3.0: Logo + 系统配置 + 审计筛选 + 角色管理 + 权限矩阵
    const logoUrl=ref('');
    const auditFilter=reactive({operator:'',operation_type:'',target_type:'',dateRange:[]});
    const rolesList=ref([]);
    const rolesLoading=ref(false);
    const roleDialog=reactive({visible:false,loading:false,isEdit:false,form:{role_id:'',role_name:'',description:''}});
    const permsMatrixData=ref([]);
    const permsMenuList=ref([]);
    const permsLoading=ref(false);
    const visibleMenus=ref(new Set(['dashboard','audit-sql','file-audit','rules','slow-tasks','slow-records','explain','instances','bigtable','deep-diag','deep-diag-cluster','deep-diag-daily','deep-diag-index','deep-diag-diff','deep-diag-emergency','deep-diag-sqlstats','deep-diag-gateway','deep-diag-ppt','deep-diag-toolkit','projects','rulesets','gate','monitor','inspection','sys-users','sys-retention','sys-auditlog','sys-info','sys-roles','sys-perms']));
    // V3.0: 表名中文映射
    const tableNameLabel=(t)=>({slow_queries:'慢SQL记录',audit_history:'审核历史',scan_tasks:'扫描任务',alerts:'告警记录',operation_logs:'操作日志',gate_audit_logs:'门禁审计日志',fingerprint_stats:'SQL指纹统计'}[t]||t);
    // V3.0: 监控指标中文映射
    const metricLabel=(m)=>({threads_running:'活跃线程数',seconds_behind_master:'主从延迟(秒)',lock_wait_count:'锁等待数',long_transaction_count:'长事务数',cpu_usage:'CPU使用率',memory_usage:'内存使用率',disk_usage:'磁盘使用率',connection_count:'连接数',slow_query_count:'慢查询数量',e2e_test_metric:'端到端测试指标',sit_critical_metric:'SIT关键指标',sit_normal_metric:'SIT常规指标',sit_test_metric:'SIT测试指标',test_metric:'测试指标',uat_cpu:'UAT-CPU'}[m]||m);
    // RBAC权限 (P2-17: 对照附录B校正)
    const roleLabel=computed(()=>({admin:'系统管理员',dba:'DBA',developer:'开发',auditor:'审计员'}[authState.role]||''));
        const roleLabelFn=(r)=>({admin:'系统管理员',dba:'DBA',developer:'开发',auditor:'审计员'}[r]||r);
    const canManagePlatform=computed(()=>['admin','dba'].includes(authState.role));
    const canManageInstances=computed(()=>['admin','dba'].includes(authState.role));
    const canViewAuditLog=computed(()=>['admin','dba','auditor'].includes(authState.role));
    const canViewSysInfo=computed(()=>['admin','dba','auditor'].includes(authState.role));
    const canViewProjects=computed(()=>['admin','dba','developer','auditor'].includes(authState.role));
    const canViewMonitor=computed(()=>['admin','dba','auditor'].includes(authState.role));
    const canViewSchedule=computed(()=>['admin','dba'].includes(authState.role));
    const canViewBigtable=computed(()=>['admin','dba','auditor'].includes(authState.role));
    const breadcrumbItems=computed(()=>{const m={dashboard:[{key:'d',label:'工作台'},{key:'c',label:'治理概览'}],'audit-sql':[{key:'a',label:'SQL审核'},{key:'c',label:'即时审核'}],'file-audit':[{key:'a',label:'SQL审核'},{key:'c',label:'文件审核'}],'schema-extractor-audit':[{key:'a',label:'SQL审核'},{key:'c',label:'在线元数据审核'}],rules:[{key:'a',label:'SQL审核'},{key:'c',label:'审核规则库'}],'slow-tasks':[{key:'s',label:'慢SQL治理'},{key:'c',label:'扫描任务'}],'slow-records':[{key:'s',label:'慢SQL治理'},{key:'c',label:'慢SQL记录'}],'slow-schedule':[{key:'s',label:'慢SQL治理'},{key:'c',label:'扫描计划'}],explain:[{key:'s',label:'慢SQL治理'},{key:'c',label:'EXPLAIN分析'}],instances:[{key:'i',label:'实例与体检'},{key:'c',label:'实例管理'}],'schema-check':[{key:'i',label:'实例与体检'},{key:'c',label:'上线检查'}],bigtable:[{key:'i',label:'实例与体检'},{key:'c',label:'大表治理'}],projects:[{key:'p',label:'平台治理'},{key:'c',label:'项目管理'}],rulesets:[{key:'p',label:'平台治理'},{key:'c',label:'规则集'}],gate:[{key:'p',label:'平台治理'},{key:'c',label:'质量门禁'}],monitor:[{key:'p',label:'平台治理'},{key:'c',label:'监控告警'}],inspection:[{key:'p',label:'平台治理'},{key:'c',label:'巡检管理'}],'sys-users':[{key:'sys',label:'系统管理'},{key:'c',label:'用户管理'}],'sys-retention':[{key:'sys',label:'系统管理'},{key:'c',label:'数据保留'}],'sys-auditlog':[{key:'sys',label:'系统管理'},{key:'c',label:'操作审计'}],'sys-info':[{key:'sys',label:'系统管理'},{key:'c',label:'系统信息'}],'sys-roles':[{key:'sys',label:'系统管理'},{key:'c',label:'角色管理'}],'sys-perms':[{key:'sys',label:'系统管理'},{key:'c',label:'权限矩阵'}]};return m[currentPage.value]||[]});
    const kpiCards=computed(()=>{const a=stats.value.audit||{};const s=stats.value.slow_queries||{};return[{key:'audit_today',label:'今日审核',value:a.today_count||0,color:'var(--brand-500)',sub:`通过 ${a.today_passed||0} / 拦截 ${a.today_failed||0}`,onClick:()=>currentPage.value='audit-sql'},{key:'pass_rate',label:'今日通过率',value:(a.today_pass_rate||0).toFixed(1)+'%',color:(a.today_pass_rate||0)>=80?'var(--success-500)':'var(--danger-500)',sub:`ERROR ${a.today_errors||0} / WARNING ${a.today_warnings||0}`},{key:'slow_pending',label:'待处理慢SQL',value:s.pending||0,color:'var(--warning-500)',sub:`严重 ${s.critical_count||0}`,onClick:()=>{currentPage.value='slow-records';slowFilters.status='pending';loadSlowList()}},{key:'slow_optimized',label:'已优化慢SQL',value:s.optimized||0,color:'var(--success-500)'}]});
    const formatTime=(iso)=>{if(!iso)return'';try{const d=new Date(iso);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}catch{return iso}};
    // P1-06: 修复CRITICAL级别显示为绿色问题
    const sevTagType=(s)=>{const v=(s||'').toUpperCase();return(v==='ERROR'||v==='CRITICAL')?'danger':v==='WARNING'?'warning':v==='INFO'?'info':'success'};
    const statusLabel=(s)=>({pending:'待处理',optimized:'已优化',ignored:'已忽略'}[s]||s);
    const sourceLabel=(s)=>({digest:'性能摘要',processlist:'进程快照',manual:'手动录入'}[s]||s);
    const categoryOrder=[{key:'naming',label:'命名规范'},{key:'ddl',label:'DDL规范'},{key:'dml',label:'DML规范'},{key:'index',label:'索引规范'},{key:'distributed',label:'分布式规范'},{key:'security',label:'安全规范'},{key:'performance',label:'性能规范'},{key:'transaction',label:'事务规范'},{key:'oracle_compat',label:'Oracle迁移兼容'}];
    const filteredCategories=computed(()=>{if(!ruleSearch.value)return categoryOrder;const q=ruleSearch.value.toLowerCase();return categoryOrder.filter(c=>{const rs=rulesByCategory.value[c.key]||[];return rs.some(r=>r.rule_id.toLowerCase().includes(q)||r.description.toLowerCase().includes(q))})});
    const filteredDailyInstDiffs = computed(() => {
      if (!dailyCompareResult.value) return [];
      const list = dailyCompareResult.value.instance_diffs || [];
      const q = dailyInstSearch.value.trim().toLowerCase();
      const sig = dailyInstSigOnly.value;
      return list.filter(item => {
        const matchQ = !q || item.node.toLowerCase().includes(q) || item.metric_label.toLowerCase().includes(q);
        const matchSig = !sig || !!item.significant;
        return matchQ && matchSig;
      });
    });

    const filteredDailySrvDiffs = computed(() => {
      if (!dailyCompareResult.value) return [];
      const list = dailyCompareResult.value.server_diffs || [];
      const q = dailySrvSearch.value.trim().toLowerCase();
      const sig = dailySrvSigOnly.value;
      return list.filter(item => {
        const matchQ = !q || item.ip.toLowerCase().includes(q) || item.hostname.toLowerCase().includes(q) || item.metric_label.toLowerCase().includes(q);
        const matchSig = !sig || !!item.significant;
        return matchQ && matchSig;
      });
    });

    const applyUser=(u)=>{authState.user=u;authState.role=u.role};
    const doLogin=async()=>{if(!loginForm.username||!loginForm.password){loginError.value='请输入用户名和口令';return}loginLoading.value=true;loginError.value='';try{const resp=await fetch(`${API_BASE}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:loginForm.username,password:loginForm.password})});const data=await resp.json();if(!resp.ok){loginError.value=data.detail||'登录失败';return}setToken(data.token);authState.token=data.token;applyUser(data.user);loginForm.password='';if(data.user.must_change_password){ElementPlus.ElMessage.warning('首次登录请修改口令');pwdDialog.visible=true;pwdDialog.forced=true}else{await loadAll()}}catch(e){loginError.value='登录请求失败: '+e.message}finally{loginLoading.value=false}};
    const doLogout=async()=>{try{await apiFetch(`${API_BASE}/api/v1/auth/logout`,{method:'POST'})}catch(e){}clearToken();authState.token='';authState.user=null;loginForm.username='';loginForm.password=''};
    const changePassword=async()=>{if(!pwdDialog.new_password){ElementPlus.ElMessage.warning('请输入新口令');return}pwdDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/change-password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:pwdDialog.old_password,new_password:pwdDialog.new_password})});const data=await resp.json();if(!resp.ok){ElementPlus.ElMessage.error(data.detail||'修改失败');return}ElementPlus.ElMessage.success('口令修改成功，请重新登录');pwdDialog.visible=false;pwdDialog.forced=false;pwdDialog.old_password='';pwdDialog.new_password='';doLogout()}catch(e){ElementPlus.ElMessage.error('修改失败: '+e.message)}finally{pwdDialog.loading=false}};
    const checkSession=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/me`);if(resp.ok){const u=await resp.json();applyUser(u);if(u.must_change_password){ElementPlus.ElMessage.warning('首次登录请修改口令');pwdDialog.visible=true;pwdDialog.forced=true}return true}}catch(e){}return false};
    const onUserCommand=(cmd)=>{if(cmd==='password'){pwdDialog.visible=true;pwdDialog.forced=false;pwdDialog.old_password='';pwdDialog.new_password=''}else if(cmd==='logout'){doLogout()}};
    const onMenuSelect=(key)=>{currentPage.value=key};
    // P1-04: 切换实例后刷新数据
    const onConnectionSwitch=async(connId)=>{if(!connId)return;localStorage.setItem('tdsql_conn',connId);try{const conn=savedConnections.value.find(c=>c.id===connId);if(conn&&!conn.active){const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/connections/${connId}/connect`,{method:'POST'});if(resp.ok){ElementPlus.ElMessage.success('实例已连接');tdsqlStatus.value={connected:true}}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'连接失败')}}loadAll()}catch(e){ElementPlus.ElMessage.error('切换实例失败: '+e.message)}};
    // P1-03: 项目切换后刷新受影响页面
    const onProjectSwitch=()=>{if(currentPage.value==='audit-sql'||currentPage.value==='file-audit'){ElementPlus.ElMessage.info('项目已切换，审核将使用项目规则集')}};
    // P1-02: 加载项目列表
    const loadProjects=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/projects`);if(resp.ok){const d=await resp.json();projects.value=d.data||[]}}catch(e){}};
    const loadDashboard=async()=>{statsLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/dashboard/summary`);if(resp.ok)stats.value=await resp.json();loadRuleHits()}catch(e){}finally{statsLoading.value=false}};
    const loadRuleHits=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/dashboard/rule-stats`);if(resp.ok)ruleHits.value=(await resp.json()).rules||[]}catch(e){}};
    const renderTrendChart=async()=>{const el=trendChartRef.value;if(!el)return;try{const resp=await apiFetch(`${API_BASE}/api/v1/dashboard/audit-trend?days=7`);const td=resp.ok?await resp.json():{dates:[],passed:[],failed:[]};const dk=isDarkTheme();const cTxt=dk?'#c3d3ec':'#475569';const cAxis=dk?'#8aa2c6':'#64748b';const cLine=dk?'#22345f':'#e2e8f0';const chart=echarts.getInstanceByDom(el)||echarts.init(el);chart.setOption({textStyle:{color:cTxt},tooltip:{trigger:'axis'},legend:{data:['通过','拦截'],bottom:0,textStyle:{color:cTxt}},grid:{left:'3%',right:'4%',bottom:'15%',top:'5%',containLabel:true},xAxis:{type:'category',data:td.dates||[],axisLabel:{color:cAxis},axisLine:{lineStyle:{color:cLine}}},yAxis:{type:'value',minInterval:1,axisLabel:{color:cAxis},splitLine:{lineStyle:{color:cLine}}},series:[{name:'通过',type:'bar',stack:'t',data:td.passed||[],itemStyle:{color:'#16a34a'}},{name:'拦截',type:'bar',stack:'t',data:td.failed||[],itemStyle:{color:'#dc2626'}}]},true)}catch(e){}};
    // P1-09: 审核项目选择用独立变量
    const auditSql=async()=>{if(!sqlInput.value.trim())return;auditing.value=true;try{const body={sql:sqlInput.value};if(auditProjectId.value)body.project_id=auditProjectId.value;const resp=await apiFetch(`${API_BASE}/api/v1/audit/sql`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});auditResult.value=await resp.json()}catch(e){ElementPlus.ElMessage.error('审核请求失败: '+e.message)}finally{auditing.value=false}};
    const loadExample=(type)=>{const ex={select:'SELECT * FROM t_order WHERE user_id=123 ORDER BY RAND() LIMIT 10',create:'CREATE TABLE t_user (\n  id BIGINT NOT NULL AUTO_INCREMENT,\n  name VARCHAR(100),\n  phone VARCHAR(20),\n  status VARCHAR(10),\n  amount FLOAT,\n  created_at TIMESTAMP,\n  notes TEXT,\n  INDEX idx_name (name),\n  INDEX idx_phone (phone)\n)',update:'UPDATE t_order SET status=0',delete:'DELETE FROM t_order WHERE status=0'};sqlInput.value=ex[type]||''};
    const onFileChange=async(file)=>{if(!file||!file.raw)return;const content=await file.raw.text();try{const body={content,file_path:file.name};if(currentProjectId.value)body.project_id=currentProjectId.value;const resp=await apiFetch(`${API_BASE}/api/v1/audit/file`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});fileAuditResult.value=await resp.json();ElementPlus.ElMessage.success('文件审核完成')}catch(e){ElementPlus.ElMessage.error('文件审核失败: '+e.message)}};
    const loadFileReports=async()=>{fileReportsLoading.value=true;try{const offset=(fileReportsPage.value-1)*10;const resp=await apiFetch(`${API_BASE}/api/v1/audit/file-reports?limit=10&offset=${offset}`);if(resp.ok){const d=await resp.json();fileReports.value=d.items||[];fileReportsTotal.value=d.total||0}}catch(e){}finally{fileReportsLoading.value=false}};
    const downloadFileReport=(reportId)=>{const t=getToken();window.open(`${API_BASE}/api/v1/audit/file-reports/${reportId}/html?access_token=${t}`,'_blank')};
    const loadRules=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/rules`);if(resp.ok){const d=await resp.json();rulesList.value=d.rules||[];const by={};for(const r of rulesList.value){if(!by[r.category])by[r.category]=[];by[r.category].push(r)}rulesByCategory.value=by}}catch(e){}};
    const loadSlowList=async()=>{slowListLoading.value=true;try{const p=new URLSearchParams({limit:slowPage.size,offset:(slowPage.current-1)*slowPage.size});if(slowFilters.db_name)p.set('db_name',slowFilters.db_name);if(slowFilters.set_id)p.set('set_id',slowFilters.set_id);if(slowFilters.severity)p.set('severity',slowFilters.severity);if(slowFilters.status)p.set('status',slowFilters.status);if(slowFilters.scan_task_id)p.set('scan_task_id',slowFilters.scan_task_id);if(slowFilters.created_by)p.set('created_by',slowFilters.created_by);if(slowFilters.keyword)p.set('keyword',slowFilters.keyword);const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries?${p}`);if(resp.ok){const d=await resp.json();slowList.value=d.items||[];slowPage.total=d.total||0}}catch(e){}finally{slowListLoading.value=false}};
    const resetSlowFilter=()=>{slowFilters.db_name='';slowFilters.set_id='';slowFilters.severity='';slowFilters.status='';slowFilters.scan_task_id='';slowFilters.created_by='';slowFilters.keyword='';slowPage.current=1;loadSlowList()};
    const openSlowDetail=async(row)=>{slowDetailDrawer.value=true;slowDetail.value=row;try{const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/${row.id}`);if(resp.ok)slowDetail.value=await resp.json()}catch(e){}};
    const setSlowStatus=async(row,status)=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/${row.id}/status`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});if(resp.ok){ElementPlus.ElMessage.success('状态已更新');loadSlowList()}}catch(e){ElementPlus.ElMessage.error('更新失败')}};
    const exportSlowReport=(row)=>{const t=getToken();window.open(`${API_BASE}/api/v1/audit/slow-report/${row.id}/export?access_token=${t}`,'_blank')};
        const downloadScanReport=(taskId)=>{const t=getToken();window.open(`${API_BASE}/api/v1/slow-queries/scan-tasks/${taskId}/html?access_token=${t}`,'_blank')};
    const goSlowDetail=(r)=>{currentPage.value='slow-records';openSlowDetail(r)};
    const goExplainFromSlow=(d)=>{currentPage.value='explain';explainMode.value='sql';explainSqlInput.value=d.fingerprint||''};
    const loadScanTasks=async()=>{try{const offset=(scanTaskCurrentPage.value-1)*10;const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/scan-tasks?limit=10&offset=${offset}`);if(resp.ok){const d=await resp.json();scanTasks.value=d.items||[];scanTaskTotal.value=d.total||0}}catch(e){}};
    const onTaskSelectChange=(rows)=>{selectedTaskIds.value=new Set(rows.map(r=>r.id))};
    const deleteScanTask=async(row)=>{try{await ElementPlus.ElMessageBox.confirm(`确认删除扫描任务「${row.task_name||row.id}」？关联的慢SQL记录将一并删除。`,'删除确认',{type:'warning'})}catch(e){return}try{const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/scan-tasks/${row.id}`,{method:'DELETE'});if(resp.ok){ElementPlus.ElMessage.success('已删除');loadScanTasks()}}catch(e){ElementPlus.ElMessage.error('删除失败')}};
    const batchDeleteScanTasks=async()=>{const n=selectedTaskIds.value.size;if(!n)return;try{await ElementPlus.ElMessageBox.confirm(`确认删除选中的 ${n} 个扫描任务？`,'批量删除',{type:'warning'})}catch(e){return}batchDeleting.value=true;let ok=0;for(const id of selectedTaskIds.value){try{const r=await apiFetch(`${API_BASE}/api/v1/slow-queries/scan-tasks/${id}`,{method:'DELETE'});if(r.ok)ok++}catch(e){}}selectedTaskIds.value=new Set();batchDeleting.value=false;ElementPlus.ElMessage.success(`成功删除 ${ok} 个任务`);loadScanTasks()};
    const startScanTask=async()=>{if(!scanTaskForm.connection_id){ElementPlus.ElMessage.warning('请先选择扫描实例');return}if(!scanTimeWindow.value||scanTimeWindow.value.length<2){ElementPlus.ElMessage.warning('请选择时间窗口');return}scanTaskLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/slow-queries/fetch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:scanTaskForm.source,connection_id:scanTaskForm.connection_id,limit:scanTaskForm.limit,min_time:scanTaskForm.min_time,task_name:scanTaskForm.task_name||'',time_window_start:scanTimeWindow.value[0],time_window_end:scanTimeWindow.value[1],poll_duration:scanTaskForm.poll_duration,poll_interval:scanTaskForm.poll_interval})});const d=await resp.json();if(resp.ok){if(d.errors&&d.errors.length>0){const errMsgs=d.errors.map(e=>`${e.source}: ${e.error}`).join('; ');ElementPlus.ElMessageBox.alert(`扫描虽完成，但发生以下错误: ${errMsgs}`,'扫描警告',{confirmButtonText:'确定',type:'warning'})}else{ElementPlus.ElMessage.success(`扫描完成，抓取 ${d.fetched} 条慢SQL`)}scanDrawer.value=false;scanTaskForm.task_name='';loadScanTasks();loadSlowList()}else{ElementPlus.ElMessage.error(d.detail||'扫描失败')}}catch(e){ElementPlus.ElMessage.error('扫描失败: '+e.message)}finally{scanTaskLoading.value=false}};
    const viewTaskSlowQueries=(row)=>{currentPage.value='slow-records';slowFilters.scan_task_id=row.id;slowPage.current=1;loadSlowList()};
    const clearOrphanRecords=async()=>{try{await ElementPlus.ElMessageBox.confirm('确认清理所有无任务关联的慢SQL记录？此操作不可恢复。','清理确认',{type:'warning'})}catch(e){return}clearingOrphan.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/orphan-records`,{method:'DELETE'});if(resp.ok){const d=await resp.json();ElementPlus.ElMessage.success(d.message||'清理完成');loadSlowList()}}catch(e){ElementPlus.ElMessage.error('清理失败')}finally{clearingOrphan.value=false}};
    const analyzeExplainBySql=async()=>{if(!explainSqlInput.value.trim()||!explainConnId.value)return;analyzingExplain.value=true;explainResult.value=null;try{const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/analyze-explain-by-sql`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:explainSqlInput.value,connection_id:explainConnId.value})});if(!resp.ok){const err=await resp.json();throw new Error(err.detail||'EXPLAIN执行失败')}explainResult.value=await resp.json();ElementPlus.ElMessage.success('EXPLAIN分析完成')}catch(e){ElementPlus.ElMessage.error('分析失败: '+e.message)}finally{analyzingExplain.value=false}};
    const analyzeExplain=async()=>{if(!explainInput.value.trim())return;analyzingExplain.value=true;try{const data=JSON.parse(explainInput.value);const resp=await apiFetch(`${API_BASE}/api/v1/slow-queries/analyze-explain`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({explain_data:Array.isArray(data)?data:[data]})});explainResult.value=await resp.json()}catch(e){ElementPlus.ElMessage.error('分析失败: '+e.message)}finally{analyzingExplain.value=false}};
    const loadSavedConnections=async()=>{connLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/connections`);if(resp.ok){const d=await resp.json();savedConnections.value=d.connections||[];if(d.default&&!currentConnectionId.value)currentConnectionId.value=d.default}}catch(e){}finally{connLoading.value=false}};
    const testConn=async()=>{connTesting.value=true;connTestResult.value=null;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/test-connection?host=${encodeURIComponent(connForm.host)}&port=${connForm.port}&user=${encodeURIComponent(connForm.username)}&password=${encodeURIComponent(connForm.password)}&database=${encodeURIComponent(connForm.database)}&monitor_host=${encodeURIComponent(connForm.monitor_host||'')}&monitor_port=${connForm.monitor_port}&monitor_user=${encodeURIComponent(connForm.monitor_user||'')}&monitor_password=${encodeURIComponent(connForm.monitor_password||'')}&monitor_db=${encodeURIComponent(connForm.monitor_db||'')}`);const d=await resp.json();if(d.status==='connected'){let msg=`业务库连接成功！${d.server_version}，延迟${d.latency_ms}ms。`;if(d.monitor_status==='connected'){msg+=` 监控库(monitordb)连接成功(发现 ${d.monitor_column_count} 列)。`;connTestResult.value={type:'success',msg:msg}}else if(d.monitor_status==='failed'){msg+=` ⚠️但监控库连接失败: ${d.monitor_error}`;connTestResult.value={type:'warning',msg:msg}}else{connTestResult.value={type:'success',msg:msg}}}else{let msg='业务库连接失败: '+(d.message||'');if(d.monitor_error){msg+=`；监控库测试失败: ${d.monitor_error}`}connTestResult.value={type:'error',msg:msg}}}catch(e){connTestResult.value={type:'error',msg:e.message}}finally{connTesting.value=false}};
    const saveConn=async()=>{if(!connForm.name){ElementPlus.ElMessage.warning('请输入连接名称');return}if(connForm.is_distributed===null||connForm.is_distributed===undefined){ElementPlus.ElMessage.warning('请选择实例类型');return}try{const body={...connForm};if(!body.id)delete body.id;if(connEditMode.value&&connForm.id){const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/connections/${connForm.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(resp.ok){ElementPlus.ElMessage.success('连接已更新');connDrawer.value=false;connEditMode.value=false;resetConnForm();loadSavedConnections()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'更新失败')}}else{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/connections`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(resp.ok){ElementPlus.ElMessage.success('连接已保存');connDrawer.value=false;connEditMode.value=false;resetConnForm();loadSavedConnections()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'保存失败')}}}catch(e){ElementPlus.ElMessage.error('保存失败: '+e.message)}};
    const resetConnForm=()=>{connForm.id='';connForm.name='';connForm.host='';connForm.port=3306;connForm.username='';connForm.password='';connForm.database='';connForm.is_distributed=true;connForm.description='';connForm.set_list='';connForm.monitor_host='';connForm.monitor_port=15001;connForm.monitor_user='';connForm.monitor_password='';connForm.monitor_db='tdsqlpcloud_monitor'};
    const openEditConn=(row)=>{connEditMode.value=true;connForm.id=row.id;connForm.name=row.name;connForm.host=row.host;connForm.port=row.port;connForm.username=row.username;connForm.password='';connForm.database=row.database||'';connForm.is_distributed=row.is_distributed!==0&&row.is_distributed!==false;connForm.description=row.description||'';connForm.set_list=row.set_list||'';connForm.monitor_host=row.monitor_host||'';connForm.monitor_port=row.monitor_port||15001;connForm.monitor_user=row.monitor_user||'';connForm.monitor_password='';connForm.monitor_db=row.monitor_db||'tdsqlpcloud_monitor';connTestResult.value=null;connDrawer.value=true};
    const openNewConn=()=>{connEditMode.value=false;resetConnForm();connTestResult.value=null;connDrawer.value=true};
    const deleteConn=async(row)=>{try{await ElementPlus.ElMessageBox.confirm(`确认删除连接「${row.name}」？`,'删除确认',{type:'warning'})}catch(e){return}try{await apiFetch(`${API_BASE}/api/v1/tdsql/connections/${row.id}`,{method:'DELETE'});ElementPlus.ElMessage.success('已删除');loadSavedConnections()}catch(e){ElementPlus.ElMessage.error('删除失败')}};
    const setDefaultConn=async(row)=>{try{await apiFetch(`${API_BASE}/api/v1/tdsql/connections/${row.id}/set-default`,{method:'POST'});ElementPlus.ElMessage.success('已设为默认');loadSavedConnections()}catch(e){ElementPlus.ElMessage.error('设置失败')}};
    const connectInstance=async(row)=>{row.connecting=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/connections/${row.id}/connect`,{method:'POST'});if(resp.ok){ElementPlus.ElMessage.success('已连接');loadSavedConnections()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'连接失败')}}catch(e){ElementPlus.ElMessage.error('连接失败')}finally{row.connecting=false}};
    const loadUsers=async()=>{usersLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users`);if(resp.ok){const d=await resp.json();usersList.value=d.users||[]}}catch(e){ElementPlus.ElMessage.error('加载用户列表失败')}finally{usersLoading.value=false}};
    const createUser=async()=>{userDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(userDialog.form)});const d=await resp.json();if(!resp.ok){ElementPlus.ElMessage.error(d.detail||'创建失败');return}ElementPlus.ElMessage.success('用户创建成功');userDialog.visible=false;loadUsers()}catch(e){ElementPlus.ElMessage.error('创建失败: '+e.message)}finally{userDialog.loading=false}};
    const openResetPwd=(row)=>{resetDialog.username=row.username;resetDialog.password='';resetDialog.visible=true};
    const resetUserPwd=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users/${resetDialog.username}/reset-password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_password:resetDialog.password})});const d=await resp.json();if(!resp.ok){ElementPlus.ElMessage.error(d.detail||'重置失败');return}ElementPlus.ElMessage.success('口令已重置');resetDialog.visible=false}catch(e){ElementPlus.ElMessage.error('重置失败: '+e.message)}};
    const unlockUser=async(row)=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users/${row.username}/unlock`,{method:'POST'});if(resp.ok){ElementPlus.ElMessage.success('已解锁');loadUsers()}}catch(e){ElementPlus.ElMessage.error('解锁失败')}};
    // P3-19: 禁用用户补二次确认
    const toggleUserStatus=async(row)=>{const ns=row.status==='active'?'disabled':'active';if(ns==='disabled'){try{await ElementPlus.ElMessageBox.confirm(`确认禁用用户 ${row.username}？禁用后该用户将无法登录。`,'禁用确认',{type:'warning'})}catch(e){return}}try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users/${row.username}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:ns})});if(resp.ok){ElementPlus.ElMessage.success(ns==='active'?'已启用':'已禁用');loadUsers()}}catch(e){}};
    const deleteUser=async(row)=>{try{await ElementPlus.ElMessageBox.confirm(`确认删除用户 ${row.username}？`,'删除确认',{type:'warning'})}catch(e){return}try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/users/${row.username}`,{method:'DELETE'});if(resp.ok){ElementPlus.ElMessage.success('用户已删除');loadUsers()}}catch(e){}};
    // P1-07: 加载活跃告警数
    const loadActiveAlerts=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/monitor/alerts`);if(resp.ok){const d=await resp.json();activeAlerts.value=(d.data||[]).length}}catch(e){}};
    // P0-01: 扫描计划
    const loadScanSchedules=async()=>{scanScheduleLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/scan-schedules`);if(resp.ok){const d=await resp.json();scanSchedules.value=d.schedules||[]}}catch(e){}finally{scanScheduleLoading.value=false}};
    const createScanSchedule=async()=>{if(!scheduleForm.connection_id){ElementPlus.ElMessage.warning('请选择目标实例');return}try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/scan-schedules`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scheduleForm)});if(resp.ok){ElementPlus.ElMessage.success('计划已创建');scheduleDrawer.value=false;loadScanSchedules()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'创建失败')}}catch(e){ElementPlus.ElMessage.error('创建失败: '+e.message)}};
    const deleteScanSchedule=async(row)=>{try{await ElementPlus.ElMessageBox.confirm(`确认删除扫描计划「${row.task_name||row.id}」？`,'删除确认',{type:'warning'})}catch(e){return}try{await apiFetch(`${API_BASE}/api/v1/tdsql/scan-schedules/${row.id}`,{method:'DELETE'});ElementPlus.ElMessage.success('已删除');loadScanSchedules()}catch(e){ElementPlus.ElMessage.error('删除失败')}};
    const toggleScheduleEnabled=async(row)=>{try{await apiFetch(`${API_BASE}/api/v1/tdsql/scan-schedules/${row.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({connection_id:row.connection_id,source:row.source||'digest',cron_hour:row.cron_hour,cron_minute:row.cron_minute,limit_rows:row.limit_rows,min_time:row.min_time,enabled:!row.enabled})});ElementPlus.ElMessage.success('已更新');loadScanSchedules()}catch(e){ElementPlus.ElMessage.error('更新失败')}};
    // P0-01: 数据库体检
    const runHealthCheck=async()=>{if(!currentConnectionId.value){ElementPlus.ElMessage.warning('请先选择实例');return}healthLoading.value=true;healthResult.value=null;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/check/${healthCheckType.value}?connection_id=${currentConnectionId.value}${healthDbName.value?'&database='+healthDbName.value:''}`);if(resp.ok){healthResult.value=await resp.json()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'检查失败')}}catch(e){ElementPlus.ElMessage.error('检查失败: '+e.message)}finally{healthLoading.value=false}};
    // 上线检查（12项Schema检查，替代tdsql_12.sh）
    const runSchemaCheck=async()=>{
      if(!schemaCheckConnId.value){ElementPlus.ElMessage.warning('请先选择实例');return}
      schemaCheckLoading.value=true;schemaCheckResults.value=[];schemaCheckSummary.value={total:0,error:0,warning:0,info:0,checks_passed:0,checks_failed:0};
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/inspection/schema-check`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({connection_id:schemaCheckConnId.value,database_filter:''})});
        if(resp.ok){
          const d=await resp.json();const data=d.data||d;
          schemaCheckSummary.value=data.summary||{total:0,error:0,warning:0,info:0,checks_passed:0,checks_failed:0};
          schemaCheckResults.value=data.results||[];
          const s=schemaCheckSummary.value;
          if(s.error>0)ElementPlus.ElMessage.warning(`检查完成：发现 ${s.total} 个问题（${s.error} ERROR / ${s.warning} WARNING / ${s.info} INFO）`);
          else ElementPlus.ElMessage.success(`检查完成：${s.checks_passed} 项通过，${s.warning+s.info} 个非严重问题`);
        }else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'检查失败')}
      }catch(e){ElementPlus.ElMessage.error('检查失败: '+e.message)}finally{schemaCheckLoading.value=false}
    };
    // ── 深度诊断：通用 POST 助手 ──
    const _deepPost=async(key,url,payload)=>{
      if(!deepConnId.value){ElementPlus.ElMessage.warning('请先选择实例');return null}
      deepLoading.value=key;
      try{
        const resp=await apiFetch(`${API_BASE}${url}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await resp.json();
        if(resp.ok){return d.data||d}
        ElementPlus.ElMessage.error(d.detail||'执行失败');return null;
      }catch(e){ElementPlus.ElMessage.error('执行失败: '+e.message);return null}
      finally{deepLoading.value=''}
    };
    const runClusterInspect=async()=>{
      const r=await _deepPost('cluster','/api/v1/cluster-inspect/run',{connection_id:deepConnId.value});
      if(r){deepResult.cluster=r;ElementPlus.ElMessage.success(`巡检完成：${r.total_issues} 项问题`)}
    };
    const runIndexAudit=async()=>{
      const r=await _deepPost('index','/api/v1/index-audit/run',{connection_id:deepConnId.value,database:deepDb.value});
      if(r){deepResult.index=r;ElementPlus.ElMessage.success(`索引体检完成：${r.total_findings} 项`)}
    };
    const runSchemaDiff=async()=>{
      if(!deepRightConnId.value){ElementPlus.ElMessage.warning('请选择对比实例');return}
      const dbs=deepDb.value?deepDb.value.split(',').map(s=>s.trim()).filter(Boolean):null;
      const r=await _deepPost('diff','/api/v1/schema-diff/run',{left_conn:deepConnId.value,right_conn:deepRightConnId.value,databases:dbs});
      if(r){deepResult.diff=r;ElementPlus.ElMessage.success(`比对完成：${r.total_items} 处差异`)}
    };
    const activeEmergencyNames=ref(['status','session','bigtrx','lock','slow','innodb']);
    const emergencyNameLabel=(n)=>({status:'实例健康与连接度',session:'非Sleep活跃会话',bigtrx:'未提交长事务',lock:'锁等待与阻塞链',slow:'正在执行的长SQL',innodb:'InnoDB死锁与引擎状态'}[n]||n);
    const runEmergency=async()=>{
      const r=await _deepPost('emergency','/api/v1/emergency/run',{connection_id:deepConnId.value,actions:['all']});
      if(r){deepResult.emergency=r;activeEmergencyNames.value=['status','session','bigtrx','lock','slow','innodb'];ElementPlus.ElMessage.success('应急诊断完成')}
    };
    const runSqlStats=async()=>{
      const r=await _deepPost('sqlstats','/api/v1/sql-stats/analyze',{connection_id:deepConnId.value,top_n:20,database:deepDb.value});
      if(r){deepResult.sqlstats=r;ElementPlus.ElMessage.success('SQL分析完成')}
    };
    // G10: ZK Discovery
    const openZkDiscovery=()=>{
      zkDialogVisible.value=true;
      zkDiscovered.value=[];
      zkSelected.value=[];
    };
    const runZkDiscovery=async()=>{
      zkScanning.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/discover`,{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify(zkForm)
        });
        if(resp.ok){
          zkDiscovered.value=await resp.json();
          ElementPlus.ElMessage.success(`发现 ${zkDiscovered.value.length} 个实例`);
        }else{
          const d=await resp.json();
          ElementPlus.ElMessage.error(d.detail||'扫描失败');
        }
      }catch(e){
        ElementPlus.ElMessage.error('发现请求失败: '+e.message);
      }finally{
        zkScanning.value=false;
      }
    };
    const handleZkSelection=(val)=>{
      zkSelected.value=val;
    };
    const registerZkInstances=async()=>{
      if(!zkSelected.value.length)return;
      zkRegistering.value=true;
      let ok=0;
      for(const inst of zkSelected.value){
        try{
          const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/discover/register`,{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
              connection_id: inst.service_name,
              service_name: inst.service_name,
              host: inst.host,
              port: inst.port,
              user: inst.user,
              password: inst.password,
              database: inst.database
            })
          });
          if(resp.ok) ok++;
        }catch(e){}
      }
      zkRegistering.value=false;
      ElementPlus.ElMessage.success(`成功导入 ${ok} 个实例`);
      zkDialogVisible.value=false;
      loadSavedConnections();
    };

    // G11: Gateway Log Analysis
    const loadGatewayReports=async()=>{
      gatewayLoading.value=true;
      try{
        const url = deepConnId.value ? `${API_BASE}/api/v1/gateway-log/reports?connection_id=${deepConnId.value}` : `${API_BASE}/api/v1/gateway-log/reports`;
        const resp=await apiFetch(url);
        if(resp.ok) gatewayReports.value=await resp.json();
      }catch(e){}
      finally{gatewayLoading.value=false}
    };
    const viewGatewayReport=async(row)=>{
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/gateway-log/reports/${row.id}`);
        if(resp.ok){
          const data = await resp.json();
          gatewayHtml.value = data.report_html || '';
          gatewayDetailVisible.value = true;
        }
      }catch(e){ElementPlus.ElMessage.error('加载报告详情失败')}
    };
    const onGatewayUpload=async(file)=>{
      if(!file||!file.raw)return;
      if(!deepConnId.value){
        ElementPlus.ElMessage.warning('请先选择左上角的实例');
        return;
      }
      gatewayLoading.value=true;
      const fd=new FormData();
      fd.append('connection_id',deepConnId.value);
      fd.append('log_type','interf');
      fd.append('file',file.raw);
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/gateway-log/upload`,{
          method:'POST',
          body:fd
        });
        const d=await resp.json();
        if(resp.ok){
          ElementPlus.ElMessage.success('日志分析完成');
          loadGatewayReports();
        }else{
          ElementPlus.ElMessage.error(d.detail||'分析失败');
        }
      }catch(e){ElementPlus.ElMessage.error('上传分析失败: '+e.message)}
      finally{gatewayLoading.value=false}
    };

    // G12: PPT Report & Dashboard
    const loadPptDashboard=async()=>{
      if(!deepConnId.value)return;
      pptLoading.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/ppt-report/dashboard?connection_id=${deepConnId.value}`);
        if(resp.ok) pptDashboard.value=await resp.json();
      }catch(e){}
      finally{pptLoading.value=false}
    };
    const generatePptReport=async()=>{
      if(!deepConnId.value){
        ElementPlus.ElMessage.warning('请先选择实例');
        return;
      }
      pptLoading.value=true;
      try{
        const t=getToken();
        window.open(`${API_BASE}/api/v1/ppt-report/generate?connection_id=${deepConnId.value}&access_token=${t}`,'_blank');
        ElementPlus.ElMessage.success('已开始 PDF 报告生成与下载');
      }catch(e){ElementPlus.ElMessage.error('导出 PDF 失败')}
      finally{pptLoading.value=false}
    };

    // G4: 每日巡检与比对报告方法
    const getDatesInRange = (startDate, endDate) => {
      const dates = [];
      let curr = new Date(startDate);
      const end = new Date(endDate);
      while (curr <= end) {
        dates.push(curr.toISOString().split('T')[0]);
        curr.setDate(curr.getDate() + 1);
      }
      return dates;
    };

    const runDailyInspect = async () => {
      if (!deepConnId.value) return ElementPlus.ElMessage.warning("请选择实例");
      if (!dailyInspectDates.value || dailyInspectDates.value.length < 2) {
        return ElementPlus.ElMessage.warning("请选择日期范围");
      }
      const dates = getDatesInRange(dailyInspectDates.value[0], dailyInspectDates.value[1]);
      if (dates.length > 3) {
        return ElementPlus.ElMessage.warning("单次手动采集时间跨度不能超过 3 天，以避免超大集群拉取超时。");
      }
      deepLoading.value = "daily_run";
      try {
        for (const d of dates) {
          await apiFetch(`${API_BASE}/api/v1/daily-inspect/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ connection_id: deepConnId.value, inspect_date: d })
          });
        }
        ElementPlus.ElMessage.success("手动巡检数据采集已完成，正在生成比对分析大屏...");
        // 自动触发比对，渲染图表和列表数据给用户
        await compareDailyInspect();
      } catch (e) {
        ElementPlus.ElMessage.error("数据采集失败: " + e.message);
      } finally {
        deepLoading.value = "";
      }
    };

    const compareDailyInspect = async () => {
      if (!deepConnId.value) return ElementPlus.ElMessage.warning("请选择实例");
      if (!dailyInspectDates.value || dailyInspectDates.value.length < 2) {
        return ElementPlus.ElMessage.warning("请选择比对日期范围");
      }
      deepLoading.value = "daily_compare";
      try {
        const d1 = dailyInspectDates.value[0];
        const d2 = dailyInspectDates.value[1];
        
        const resp = await apiFetch(`${API_BASE}/api/v1/daily-inspect/compare?connection_id=${deepConnId.value}&date1=${d1}&date2=${d2}&threshold_multiplier=${dailyInspectThreshold.value}`);
        if (resp.ok) {
          dailyCompareResult.value = await resp.json();
        }
        
        const trendResp = await apiFetch(`${API_BASE}/api/v1/daily-inspect/trend?connection_id=${deepConnId.value}&date_from=${d1}&date_to=${d2}`);
        if (trendResp.ok) {
          dailyInspectChartData.value = await trendResp.json();
          
          const nodes = new Set();
          const s = dailyInspectChartData.value.series;
          Object.keys(s).forEach(k => {
            s[k].forEach(item => nodes.add(item.node));
          });
          dailyInspectChartNodes.value = Array.from(nodes);
          if (dailyInspectChartNodes.value.length > 0 && !dailyInspectChartNode.value) {
            dailyInspectChartNode.value = dailyInspectChartNodes.value[0];
          }
          
          setTimeout(renderDailyTrendChart, 100);
        }
        
        ElementPlus.ElMessage.success("差异比对完成");
      } catch (e) {
        ElementPlus.ElMessage.error("比对失败: " + e.message);
      } finally {
        deepLoading.value = "";
      }
    };

    let dailyTrendChartInstance = null;
    const renderDailyTrendChart = () => {
      const el = dailyTrendChartRef.value;
      if (!el || !dailyInspectChartData.value) return;
      
      if (!dailyTrendChartInstance) {
        dailyTrendChartInstance = echarts.init(el);
      }
      
      const metric = dailyInspectChartMetric.value;
      const node = dailyInspectChartNode.value;
      const seriesData = dailyInspectChartData.value.series[metric] || [];
      
      const filtered = seriesData.filter(item => item.node === node);
      filtered.sort((a, b) => a.date.localeCompare(b.date));
      
      const dates = filtered.map(item => item.date);
      const values = filtered.map(item => item.value);
      const _dk = isDarkTheme();
      const _cAxis = _dk ? '#94a3b8' : '#64748b';
      const _cLine = _dk ? '#334155' : '#e2e8f0';

      dailyTrendChartInstance.setOption({
        tooltip: { trigger: 'axis' },
        grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
        xAxis: {
          type: 'category',
          data: dates,
          axisLabel: { color: _cAxis }
        },
        yAxis: {
          type: 'value',
          axisLabel: { color: _cAxis },
          splitLine: { lineStyle: { color: _cLine } }
        },
        series: [{
          data: values,
          type: 'line',
          smooth: true,
          itemStyle: { color: '#3b82f6' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
              { offset: 1, color: 'rgba(59, 130, 246, 0.0)' }
            ])
          }
        }]
      });
    };

    const exportDailyHtmlReport = () => {
      if (!deepConnId.value || !dailyInspectDates.value || dailyInspectDates.value.length < 2) return;
      const d1 = dailyInspectDates.value[0];
      const d2 = dailyInspectDates.value[1];
      const t = getToken();
      window.open(`${API_BASE}/api/v1/daily-inspect/compare/html?connection_id=${deepConnId.value}&date1=${d1}&date2=${d2}&threshold_multiplier=${dailyInspectThreshold.value}&access_token=${t}`, '_blank');
    };

    // G13: Ops Toolkit
    const loadToolkitScripts=async()=>{
      toolkitLoading.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/toolkit/scripts`);
        if(resp.ok) toolkitScripts.value=await resp.json();
      }catch(e){}
      finally{toolkitLoading.value=false}
    };
    const downloadToolkitScript=(scriptPath)=>{
      const t=getToken();
      window.open(`${API_BASE}/api/v1/toolkit/download?file_path=${encodeURIComponent(scriptPath)}&access_token=${t}`,'_blank');
    };
    const runExtractAndAudit=async()=>{
      if(!extractedAuditConnId.value){ElementPlus.ElMessage.warning('请先选择目标实例');return}
      extractAuditing.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/audit/extract-and-audit`,{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({connection_id:extractedAuditConnId.value,database:extractedDbName.value,scopes:extractedScope.value})
        });
        if(resp.ok){
          const d=await resp.json();
          extractedResult.value=d;
          ElementPlus.ElMessage.success('成功从 SIT/UAT 数据库提取在线元数据并完成文件规则审核');
        }else{
          let msg='提取或审核失败';
          try{const d=await resp.json(); msg=d.detail||msg;}catch(err){msg=`服务端响应异常 (HTTP ${resp.status})`;}
          ElementPlus.ElMessage.error(msg);
        }
      }catch(e){ElementPlus.ElMessage.error('提取失败: '+e.message)}
      finally{extractAuditing.value=false}
    };
    const downloadExtractedSql=()=>{
      if(!extractedResult.value.extracted_sql) return;
      const blob=new Blob([extractedResult.value.extracted_sql],{type:'text/plain;charset=utf-8'});
      const url=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=url;
      a.download=extractedResult.value.filename||'extracted_schema.sql';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      ElementPlus.ElMessage.success('已下载元数据 SQL 文件');
    };
    const loadExtractedReports=async()=>{
      extractedReportsLoading.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/audit/extracted-reports`);
        if(resp.ok){
          const d=await resp.json();
          extractedReports.value=d.reports||[];
        }
      }catch(e){}
      finally{extractedReportsLoading.value=false}
    };
    const downloadExtractedHtmlReport=(reportId)=>{
      if(!reportId) return;
      const t=getToken();
      window.open(`${API_BASE}/api/v1/audit/report/${reportId}/html?access_token=${t}`,'_blank');
    };
    const downloadExtractedSqlFile=(reportId)=>{
      if(!reportId) return;
      const t=getToken();
      window.open(`${API_BASE}/api/v1/audit/report/${reportId}/sql?access_token=${t}`,'_blank');
    };
    const exportSchemaCheckReport=async()=>{
      if(!schemaCheckConnId.value){ElementPlus.ElMessage.warning('请先选择实例');return}
      schemaCheckLoading.value=true;
      try{
        const resp=await fetch(`${API_BASE}/api/v1/inspection/schema-check/report`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+getToken()},body:JSON.stringify({connection_id:schemaCheckConnId.value,database_filter:''})});
        if(resp.ok){const blob=await resp.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='上线检查报告_'+new Date().toISOString().slice(0,10)+'.html';document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);ElementPlus.ElMessage.success('报告已导出')}
        else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'导出失败')}
      }catch(e){ElementPlus.ElMessage.error('导出失败: '+e.message)}finally{schemaCheckLoading.value=false}
    };
    // P0-01: 大表治理
    const loadBigtable=async()=>{if(!currentConnectionId.value){ElementPlus.ElMessage.warning('请先选择实例');return}bigtableLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/bigtable/inventory/${currentConnectionId.value}`);if(resp.ok){const d=await resp.json();bigtableData.value=d.data||[]}}catch(e){}finally{bigtableLoading.value=false}};
    // 大表分区下钻
    const bigtableRowKey=(row)=>(row.schema_name||'')+'.'+(row.table_name||'');
    const partitionBoundaryLabel=(method)=>{const m=String(method||'').toUpperCase();if(m.indexOf('RANGE')>=0)return '边界(LESS THAN)';if(m.indexOf('LIST')>=0)return '分区值(IN)';if(m.indexOf('HASH')>=0||m.indexOf('KEY')>=0)return '哈希桶';return '边界/分区值'};
    const bigtableRowClass=({row})=>row.is_partitioned?'':'bt-no-expand';
    const togglePartitions=(row)=>{if(bigtableRef.value)bigtableRef.value.toggleRowExpansion(row)};
    const onBigtableExpand=(row,expandedRows)=>{const isOpen=Array.isArray(expandedRows)?expandedRows.includes(row):!!expandedRows;if(isOpen&&row.is_partitioned)loadTablePartitions(row)};
    const loadTablePartitions=async(row)=>{const key=bigtableRowKey(row);if(partitionDetail[key])return;if(!currentConnectionId.value){ElementPlus.ElMessage.warning('请先选择实例');return}partitionLoading[key]=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/tdsql/table-partitions?connection_id=${currentConnectionId.value}&schema=${encodeURIComponent(row.schema_name)}&table=${encodeURIComponent(row.table_name)}`);if(resp.ok){partitionDetail[key]=await resp.json()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'加载分区明细失败')}}catch(e){ElementPlus.ElMessage.error('加载分区明细失败: '+e.message)}finally{partitionLoading[key]=false}};
    // P0-01: 项目管理
    const loadProjectsList=async()=>{projectsLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/projects`);if(resp.ok){const d=await resp.json();projectsList.value=d.data||[]}}catch(e){}finally{projectsLoading.value=false}};
    const createProject=async()=>{projectDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/projects`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(projectDialog.form)});if(resp.ok){ElementPlus.ElMessage.success('项目已创建');projectDialog.visible=false;loadProjectsList();loadProjects()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'创建失败')}}catch(e){ElementPlus.ElMessage.error('创建失败: '+e.message)}finally{projectDialog.loading=false}};
    const deleteProject=async(row)=>{try{await ElementPlus.ElMessageBox.confirm(`确认删除项目「${row.project_name}」？删除后不可恢复。`,'删除确认',{type:'warning'})}catch(e){return}try{const resp=await apiFetch(`${API_BASE}/api/v1/projects/${row.project_id}`,{method:'DELETE'});if(resp.ok){ElementPlus.ElMessage.success('项目已删除');loadProjectsList();loadProjects()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'删除失败')}}catch(e){ElementPlus.ElMessage.error('删除失败: '+e.message)}};
    const toggleProjectStatus=async(row)=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/projects/${row.project_id}/toggle-status`,{method:'PUT'});if(resp.ok){const d=await resp.json();ElementPlus.ElMessage.success(d.message||'状态已更新');loadProjectsList();loadProjects()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'操作失败')}}catch(e){ElementPlus.ElMessage.error('操作失败: '+e.message)}};
    // P0-01: 规则集
    const loadRulesets=async()=>{rulesetsLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/rulesets`);if(resp.ok){const d=await resp.json();rulesets.value=d.rulesets||[]}}catch(e){}finally{rulesetsLoading.value=false}};
    
    // ⚙️ 规则集明细配置与交互
    const openRulesetConfig = async (rs) => {
      rulesetDrawer.ruleset = rs;
      rulesetDrawer.searchQuery = '';
      rulesetDrawer.categoryFilter = 'ALL';
      rulesetDrawer.visible = true;
      rulesetDrawer.loading = true;
      try {
        const [rulesResp, rsResp] = await Promise.all([
          apiFetch(`${API_BASE}/api/v1/rules`),
          apiFetch(`${API_BASE}/api/v1/rulesets/${rs.id}`)
        ]);
        let allRules = [];
        if (rulesResp.ok) {
          const d = await rulesResp.json();
          allRules = d.rules || [];
        }
        let savedItems = [];
        if (rsResp.ok) {
          const d = await rsResp.json();
          savedItems = d.items || [];
        }
        const itemMap = {};
        savedItems.forEach(i => {
          itemMap[i.rule_id] = {
            enabled: i.enabled === 1 || i.enabled === true,
            severity_override: i.severity_override || null
          };
        });
        rulesetConfigItems.value = allRules.map(r => {
          const saved = itemMap[r.rule_id];
          return {
            rule_id: r.rule_id,
            category: r.category || '通用',
            description: r.description || '',
            severity: r.severity || 'WARNING',
            enabled: saved ? saved.enabled : true,
            severity_override: saved ? saved.severity_override : null
          };
        });
      } catch (e) {
        ElementPlus.ElMessage.error('加载规则列表失败: ' + e.message);
      } finally {
        rulesetDrawer.loading = false;
      }
    };

    const rulesetCategories = computed(() => {
      const cats = new Set();
      rulesetConfigItems.value.forEach(i => { if (i.category) cats.add(i.category); });
      return Array.from(cats);
    });

    const rulesetCategoryCounts = computed(() => {
      const counts = { ALL: rulesetConfigItems.value.length };
      rulesetConfigItems.value.forEach(i => {
        const c = i.category || '通用';
        counts[c] = (counts[c] || 0) + 1;
      });
      return counts;
    });

    const filteredRulesetItems = computed(() => {
      let list = rulesetConfigItems.value;
      if (rulesetDrawer.categoryFilter && rulesetDrawer.categoryFilter !== 'ALL') {
        list = list.filter(i => i.category === rulesetDrawer.categoryFilter);
      }
      if (rulesetDrawer.searchQuery) {
        const q = rulesetDrawer.searchQuery.toLowerCase().trim();
        list = list.filter(i => (i.rule_id && i.rule_id.toLowerCase().includes(q)) || (i.description && i.description.toLowerCase().includes(q)));
      }
      return list;
    });

    const modifiedOverrideCount = computed(() => {
      return rulesetConfigItems.value.filter(i => !!i.severity_override).length;
    });

    const disabledCount = computed(() => {
      return rulesetConfigItems.value.filter(i => !i.enabled).length;
    });

    const setFilteredRulesEnabled = (enabled) => {
      filteredRulesetItems.value.forEach(i => { i.enabled = enabled; });
    };

    const resetFilteredRulesOverrides = () => {
      filteredRulesetItems.value.forEach(i => { i.severity_override = null; });
    };

    const saveRulesetConfig = async () => {
      if (!rulesetDrawer.ruleset || rulesetDrawer.ruleset.is_builtin) return;
      rulesetDrawer.saving = true;
      try {
        const itemsPayload = rulesetConfigItems.value.map(i => ({
          rule_id: i.rule_id,
          enabled: i.enabled,
          severity_override: i.severity_override || null
        }));
        const resp = await apiFetch(`${API_BASE}/api/v1/rulesets/${rulesetDrawer.ruleset.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: rulesetDrawer.ruleset.name,
            description: rulesetDrawer.ruleset.description,
            items: itemsPayload
          })
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success(`规则集「${rulesetDrawer.ruleset.name}」配置已成功保存！`);
          rulesetDrawer.visible = false;
          loadRulesets();
        } else {
          const d = await resp.json();
          ElementPlus.ElMessage.error(d.detail || '保存配置失败');
        }
      } catch (e) {
        ElementPlus.ElMessage.error('保存配置失败: ' + e.message);
      } finally {
        rulesetDrawer.saving = false;
      }
    };
    // P0-01: 质量门禁
    const loadGateRules=async()=>{if(!currentProjectId.value){gateRules.value=null;return}gateLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/gate/rules/${currentProjectId.value}`);if(resp.ok){const d=await resp.json();gateRules.value=d.data||d}else gateRules.value=null}catch(e){gateRules.value=null}finally{gateLoading.value=false}};
    const loadGateStrategies=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/gate/strategies`);if(resp.ok){const d=await resp.json();gateStrategies.value=Object.keys(d.data||{}).map(k=>({name:k,...d.data[k]}))}}catch(e){}};
    const applyGateStrategy=async(strategy)=>{if(!currentProjectId.value){ElementPlus.ElMessage.warning('请先选择项目');return}try{const resp=await apiFetch(`${API_BASE}/api/v1/gate/strategy/${currentProjectId.value}?strategy=${strategy}`,{method:'POST'});if(resp.ok){ElementPlus.ElMessage.success('策略已应用');loadGateRules()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'应用失败')}}catch(e){ElementPlus.ElMessage.error('应用失败')}};
    // P0-01: 监控告警
    const loadMonitorAlerts=async()=>{monitorLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/monitor/alerts`);if(resp.ok){const d=await resp.json();monitorAlerts.value=d.data||[]}}catch(e){}finally{monitorLoading.value=false}};
    const acknowledgeAlert=async(row)=>{try{await apiFetch(`${API_BASE}/api/v1/monitor/alerts/${row.id}/acknowledge`,{method:'POST'});ElementPlus.ElMessage.success('已确认');loadMonitorAlerts();loadActiveAlerts()}catch(e){ElementPlus.ElMessage.error('确认失败')}};
    const loadMonitorRules=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/monitor/rules`);if(resp.ok){const d=await resp.json();monitorRules.value=d.data||[]}}catch(e){}};
    // P0-01: 巡检管理
    const loadInspectionTasks=async()=>{inspectionLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/inspection/tasks`);if(resp.ok){const d=await resp.json();inspectionTasks.value=d.data||[]}}catch(e){}finally{inspectionLoading.value=false}};
    // P0-01: 操作审计日志
    const loadAuditLogs=async()=>{auditLogsLoading.value=true;try{const p=new URLSearchParams({limit:20,offset:(auditLogsPage.value-1)*20});if(auditFilter.operator)p.set('operator',auditFilter.operator);if(auditFilter.operation_type)p.set('operation_type',auditFilter.operation_type);if(auditFilter.target_type)p.set('target_type',auditFilter.target_type);if(auditFilter.dateRange&&auditFilter.dateRange.length===2){p.set('start_date',auditFilter.dateRange[0]);p.set('end_date',auditFilter.dateRange[1])}const resp=await apiFetch(`${API_BASE}/api/v1/admin/operation-logs?${p}`);if(resp.ok){const d=await resp.json();auditLogs.value=d.logs||d.items||[];auditLogsTotal.value=d.total||0}}catch(e){}finally{auditLogsLoading.value=false}};
    // P0-01: 数据保留
    const loadRetention=async()=>{retentionLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/retention`);if(resp.ok){const d=await resp.json();retentionPolicies.value=d.policies||[]}}catch(e){}finally{retentionLoading.value=false}};
    const runRetentionCleanup=async()=>{try{await ElementPlus.ElMessageBox.confirm('确认立即执行数据清理？此操作将删除超过保留期限的数据。','清理确认',{type:'warning'})}catch(e){return}try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/retention/run`,{method:'POST'});if(resp.ok){const d=await resp.json();ElementPlus.ElMessage.success(d.message||'清理完成');loadRetention()}}catch(e){ElementPlus.ElMessage.error('清理失败')}};
    // P0-01: 系统信息
    const loadSysInfo=async()=>{sysInfoLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/info`);if(resp.ok)sysInfo.value=await resp.json()}catch(e){}finally{sysInfoLoading.value=false}};
    // V3.0: Logo
    const loadLogo=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/logo`);if(resp.ok){const d=await resp.json();logoUrl.value=d.logo_url||'';if(logoUrl.value)logoUrl.value+='?t='+Date.now()}}catch(e){}};
    const onLogoUpload=async(file)=>{const fd=new FormData();fd.append('file',file);try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/logo`,{method:'POST',body:fd});if(resp.ok){const d=await resp.json();logoUrl.value=d.logo_url+'?t='+Date.now();ElementPlus.ElMessage.success('Logo上传成功')}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'上传失败')}}catch(e){ElementPlus.ElMessage.error('上传失败: '+e.message)}return false};
    const resetLogo=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/logo`,{method:'DELETE'});if(resp.ok){logoUrl.value='';ElementPlus.ElMessage.success('已恢复默认')}}catch(e){ElementPlus.ElMessage.error('操作失败')}};
    // V3.0: 系统配置开关
    const toggleSysConfig=async(key,val)=>{try{const body={};body[key]=val;const resp=await apiFetch(`${API_BASE}/api/v1/admin/config`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(resp.ok){sysInfo.value[key]=val;ElementPlus.ElMessage.success('配置已更新')}else{ElementPlus.ElMessage.error('更新失败')}}catch(e){ElementPlus.ElMessage.error('更新失败')}};
    // V3.0: 审计筛选
    const resetAuditFilter=()=>{auditFilter.operator='';auditFilter.operation_type='';auditFilter.target_type='';auditFilter.dateRange=[];auditLogsPage.value=1;loadAuditLogs()};
    // V3.0: 角色管理
    const loadRoles=async()=>{rolesLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/roles`);if(resp.ok){const d=await resp.json();rolesList.value=d.roles||[]}}catch(e){}finally{rolesLoading.value=false}};
    const openRoleEdit=(row)=>{roleDialog.isEdit=true;roleDialog.form={role_id:row.role_id,role_name:row.role_name,description:row.description||''};roleDialog.visible=true};
    const saveRole=async()=>{
      if(!roleDialog.form.role_name){ElementPlus.ElMessage.warning('请输入角色名称');return}
      roleDialog.loading=true;
      try{
        if(roleDialog.isEdit){
          const resp=await apiFetch(`${API_BASE}/api/v1/auth/roles/${roleDialog.form.role_id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({role_name:roleDialog.form.role_name,description:roleDialog.form.description})});
          if(resp.ok){ElementPlus.ElMessage.success('角色已更新');roleDialog.visible=false;loadRoles()}
          else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'更新失败')}
        }else{
          const resp=await apiFetch(`${API_BASE}/api/v1/auth/roles`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(roleDialog.form)});
          if(resp.ok){ElementPlus.ElMessage.success('角色已创建');roleDialog.visible=false;roleDialog.form={role_id:'',role_name:'',description:''};loadRoles()}
          else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'创建失败')}
        }
      }catch(e){ElementPlus.ElMessage.error('操作失败: '+e.message)}finally{roleDialog.loading=false}
    };
    const deleteRole=async(row)=>{if(row.is_builtin){ElementPlus.ElMessage.warning('内置角色不可删除');return}try{await ElementPlus.ElMessageBox.confirm(`确认删除角色「${row.role_name}」？`,'删除确认',{type:'warning'})}catch(e){return}try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/roles/${row.role_id}`,{method:'DELETE'});if(resp.ok){ElementPlus.ElMessage.success('已删除');loadRoles()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'删除失败')}}catch(e){ElementPlus.ElMessage.error('删除失败')}};
    // V3.0: 权限矩阵
    const loadPerms=async()=>{permsLoading.value=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/role-permissions`);if(resp.ok){const d=await resp.json();permsMenuList.value=d.menus||[];const roleMap={};for(const p of(d.permissions||[])){if(!roleMap[p.role_id])roleMap[p.role_id]={role_id:p.role_id,role_name:p.role_name};roleMap[p.role_id][p.menu_key]=!!p.visible}permsMatrixData.value=Object.values(roleMap)}}catch(e){}finally{permsLoading.value=false}};
    const onPermChange=async(roleId,menuKey,val)=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/role-permissions/${roleId}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({permissions:{[menuKey]:val?1:0}})});if(resp.ok){ElementPlus.ElMessage.success('权限已更新');loadPerms()}else{ElementPlus.ElMessage.error('更新失败')}}catch(e){ElementPlus.ElMessage.error('更新失败')}};
    // 第三节增删改
    const collectBigtable=async()=>{if(!currentConnectionId.value){ElementPlus.ElMessage.warning('请先选择实例');return}bigtableCollecting.value=true;try{const cr=await apiFetch(`${API_BASE}/api/v1/tdsql/check/large-tables?connection_id=${currentConnectionId.value}`);if(!cr.ok){const d=await cr.json();ElementPlus.ElMessage.error(d.detail||'采集失败');return}const cd=await cr.json();const tables=(cd.tables||[]).map(t=>({schema:t.schema_name||'',table:t.table_name,size_gb:t.size_gb,rows:t.rows_count,level:t.level||'',is_partitioned:!!t.is_partitioned,partition_count:t.partition_count||0,shard_key:t.shard_key||''}));if(!tables.length){ElementPlus.ElMessage.info('未发现大表');return}const sr=await apiFetch(`${API_BASE}/api/v1/bigtable/inventory/${currentConnectionId.value}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(tables)});if(sr.ok){ElementPlus.ElMessage.success(`已采集 ${tables.length} 张大表`);loadBigtable()}else{const d=await sr.json();ElementPlus.ElMessage.error(d.detail||'保存失败')}}catch(e){ElementPlus.ElMessage.error('采集失败: '+e.message)}finally{bigtableCollecting.value=false}};
    const createInspection=async()=>{if(!inspectionDialog.form.connection_id){ElementPlus.ElMessage.warning('请选择实例');return}inspectionDialog.loading=true;try{const q=new URLSearchParams({connection_id:inspectionDialog.form.connection_id,inspection_type:inspectionDialog.form.inspection_type});const resp=await apiFetch(`${API_BASE}/api/v1/inspection/tasks?${q}`,{method:'POST'});if(resp.ok){ElementPlus.ElMessage.success('巡检任务已创建');inspectionDialog.visible=false;loadInspectionTasks()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'创建失败')}}catch(e){ElementPlus.ElMessage.error('创建失败: '+e.message)}finally{inspectionDialog.loading=false}};
    const viewInspectionResult=async(row)=>{inspectionResultDrawer.value=true;inspectionResults.value=[];try{const resp=await apiFetch(`${API_BASE}/api/v1/inspection/tasks/${row.id}`);if(resp.ok){const d=await resp.json();const data=d.data||d;inspectionResults.value=data.results||data.inspection_results||[]}}catch(e){}};
    const createRuleset=async()=>{if(!rulesetDialog.form.id||!rulesetDialog.form.name){ElementPlus.ElMessage.warning('请输入规则集ID和名称');return}rulesetDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/rulesets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:rulesetDialog.form.id,name:rulesetDialog.form.name,description:rulesetDialog.form.description,items:[]})});if(resp.ok){ElementPlus.ElMessage.success('规则集已创建');rulesetDialog.visible=false;rulesetDialog.form={id:'',name:'',description:''};loadRulesets()}else{const d=await resp.json();ElementPlus.ElMessage.error((d.detail&&d.detail[0]&&d.detail[0].msg)||d.detail||'创建失败')}}catch(e){ElementPlus.ElMessage.error('创建失败: '+e.message)}finally{rulesetDialog.loading=false}};
    const deleteRuleset=async(row)=>{if(row.is_builtin){ElementPlus.ElMessage.warning('内置规则集不可删除');return}try{await ElementPlus.ElMessageBox.confirm(`确认删除规则集「${row.name}」？`,'删除确认',{type:'warning'})}catch(e){return}try{await apiFetch(`${API_BASE}/api/v1/rulesets/${row.id}`,{method:'DELETE'});ElementPlus.ElMessage.success('已删除');loadRulesets()}catch(e){ElementPlus.ElMessage.error('删除失败')}};
    const createMonitorRule=async()=>{if(!monitorRuleDialog.form.metric_name){ElementPlus.ElMessage.warning('请输入指标名');return}monitorRuleDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/monitor/rules`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(monitorRuleDialog.form)});if(resp.ok){ElementPlus.ElMessage.success('告警规则已保存');monitorRuleDialog.visible=false;loadMonitorRules()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'保存失败')}}catch(e){ElementPlus.ElMessage.error('保存失败: '+e.message)}finally{monitorRuleDialog.loading=false}};
    const openGateCustom=()=>{if(gateRules.value){gateCustom.max_error_count=gateRules.value.max_error_count||0;gateCustom.max_warning_count=gateRules.value.max_warning_count||10}gateCustom.visible=true};
    const saveGateCustom=async()=>{if(!currentProjectId.value){ElementPlus.ElMessage.warning('请先选择项目');return}try{const resp=await apiFetch(`${API_BASE}/api/v1/gate/rules`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:currentProjectId.value,max_error_count:gateCustom.max_error_count,max_warning_count:gateCustom.max_warning_count})});if(resp.ok){ElementPlus.ElMessage.success('门禁规则已保存');gateCustom.visible=false;loadGateRules()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'保存失败')}}catch(e){ElementPlus.ElMessage.error('保存失败: '+e.message)}};
    const openRetentionEdit=(row)=>{if(row){retentionEditMode.value=true;retentionDialog.form={table_name:row.table_name,retention_days:row.retention_days,enabled:!!row.enabled}}else{retentionEditMode.value=false;retentionDialog.form={table_name:'',retention_days:30,enabled:true}}retentionDialog.visible=true};
    const saveRetention=async()=>{if(!retentionDialog.form.table_name){ElementPlus.ElMessage.warning('请输入表名');return}retentionDialog.loading=true;try{const resp=await apiFetch(`${API_BASE}/api/v1/admin/retention`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(retentionDialog.form)});if(resp.ok){ElementPlus.ElMessage.success('保留策略已保存');retentionDialog.visible=false;loadRetention()}else{const d=await resp.json();ElementPlus.ElMessage.error(d.detail||'保存失败')}}catch(e){ElementPlus.ElMessage.error('保存失败: '+e.message)}finally{retentionDialog.loading=false}};
    const loadVisibleMenus=async()=>{try{const resp=await apiFetch(`${API_BASE}/api/v1/auth/visible-menus`);if(resp.ok){const d=await resp.json();visibleMenus.value=new Set(d.menus||[])}}catch(e){}};
    const loadAll=async()=>{
      await loadVisibleMenus();
      loadLogo();
      if(visibleMenus.value.has('dashboard')){loadDashboard();loadActiveAlerts()}
      if(visibleMenus.value.has('instances'))loadSavedConnections();
      if(visibleMenus.value.has('rules'))loadRules();
      if(visibleMenus.value.has('slow-tasks'))loadScanTasks();
      if(visibleMenus.value.has('slow-records'))loadSlowList();
      if(visibleMenus.value.has('projects'))loadProjects();
      const menuOrder=['dashboard','sql-audit','file-audit','schema-extractor-audit','instances','slow-overview','slow-records','slow-tasks','slow-schedule','bigtable','deep-diag','projects','rulesets','gate','monitor','inspection','sys-users','sys-roles','sys-perms','sys-auditlog','sys-retention','sys-info'];
      if(!visibleMenus.value.has(currentPage.value)){
        for(const m of menuOrder){
          if(visibleMenus.value.has(m)){currentPage.value=m;break}
        }
      }
    };
    onMounted(async()=>{onUnauthorized=()=>{authState.token='';authState.user=null};const ok=await checkSession();if(ok&&!pwdDialog.forced)await loadAll()});
    watch(currentPage,(v)=>{if(v==='dashboard')nextTick(renderTrendChart);if(v==='instances')loadSavedConnections();if(v==='rules'&&rulesList.value.length===0)loadRules();if(v==='file-audit'&&fileAuditTab.value==='reports')loadFileReports();if(v==='schema-extractor-audit'&&extractedTab.value==='history')loadExtractedReports();if(v==='slow-tasks')loadScanTasks();if(v==='slow-records')loadSlowList();if(v==='sys-users')loadUsers();if(v==='slow-schedule')loadScanSchedules();if(v==='bigtable')loadBigtable();if(v==='projects')loadProjectsList();if(v==='rulesets')loadRulesets();if(v==='gate'){loadGateStrategies();loadGateRules()};if(v==='monitor'){loadMonitorAlerts();loadMonitorRules()};if(v==='inspection')loadInspectionTasks();if(v==='sys-auditlog')loadAuditLogs();if(v==='sys-retention')loadRetention();if(v==='sys-info')loadSysInfo();if(v==='sys-roles')loadRoles();if(v==='sys-perms')loadPerms();if(v==='deep-diag'){const subtabs=[{perm:'deep-diag-cluster',tab:'cluster'},{perm:'deep-diag-daily',tab:'daily_inspect'},{perm:'deep-diag-index',tab:'index'},{perm:'deep-diag-diff',tab:'diff'},{perm:'deep-diag-emergency',tab:'emergency'},{perm:'deep-diag-sqlstats',tab:'sqlstats'},{perm:'deep-diag-gateway',tab:'gateway_log'},{perm:'deep-diag-ppt',tab:'ppt_report'},{perm:'deep-diag-toolkit',tab:'toolkit'}];for(const t of subtabs){if(visibleMenus.value.has(t.perm)){deepTab.value=t.tab;break}}if(deepTab.value==='gateway_log')loadGatewayReports();if(deepTab.value==='ppt_report')loadPptDashboard();if(deepTab.value==='toolkit')loadToolkitScripts()}});
    watch(fileAuditTab,(v)=>{if(v==='reports')loadFileReports()});
    watch(extractedTab,(v)=>{if(v==='history')loadExtractedReports()});
    watch(deepTab,(v)=>{if(v==='gateway_log')loadGatewayReports();if(v==='ppt_report')loadPptDashboard();if(v==='toolkit')loadToolkitScripts()});
    watch(deepConnId,(v)=>{if(v){if(deepTab.value==='gateway_log')loadGatewayReports();if(deepTab.value==='ppt_report')loadPptDashboard()}});
    return{currentPage,sidebarCollapsed,theme,toggleTheme,authState,loginForm,loginLoading,loginError,pwdDialog,savedConnections,currentConnectionId,projects,currentProjectId,activeAlerts,metadataEnhanced,statsLoading,stats,ruleHits,trendChartRef,kpiCards,sqlInput,auditing,auditResult,auditProjectId,fileAuditTab,fileAuditResult,fileReports,fileReportsLoading,fileReportsTotal,fileReportsPage,rulesList,rulesByCategory,ruleSearch,expandedCategories,filteredCategories,slowList,slowListLoading,slowFilters,slowPage,scanTasks,scanTaskTotal,scanTaskCurrentPage,scanTaskLoading,selectedTaskIds,batchDeleting,clearingOrphan,scanDrawer,scanTimeWindow,scanTaskForm,slowDetailDrawer,slowDetail,explainMode,explainSqlInput,explainInput,explainConnId,analyzingExplain,explainResult,tdsqlStatus,connDrawer,connForm,connEditMode,connTestResult,connTesting,connLoading,usersList,usersLoading,userDialog,resetDialog,scanSchedules,scanScheduleLoading,scheduleDrawer,scheduleForm,healthLoading,healthResult,healthCheckType,healthDbName,schemaCheckConnId,schemaCheckScope,schemaCheckResults,schemaCheckSummary,schemaCheckLoading,extractedAuditConnId,extractedDbName,extractedScope,extractAuditing,extractedResult,runExtractAndAudit,downloadExtractedSql,bigtableLoading,bigtableData,bigtableRef,partitionDetail,partitionLoading,projectsList,projectsLoading,projectDialog,rulesets,rulesetsLoading,gateRules,gateStrategies,gateLoading,monitorAlerts,monitorRules,monitorLoading,monitorTab,inspectionTasks,inspectionLoading,auditLogs,auditLogsLoading,auditLogsTotal,auditLogsPage,retentionPolicies,retentionLoading,sysInfo,sysInfoLoading,roleLabel,canManagePlatform,canManageInstances,canViewAuditLog,canViewSysInfo,canViewProjects,canViewMonitor,canViewSchedule,canViewBigtable,breadcrumbItems,formatTime,sevTagType,statusLabel,sourceLabel,categoryOrder,doLogin,doLogout,changePassword,onUserCommand,onMenuSelect,onConnectionSwitch,onProjectSwitch,auditSql,loadExample,onFileChange,loadFileReports,downloadFileReport,loadRules,loadSlowList,resetSlowFilter,openSlowDetail,setSlowStatus,exportSlowReport,downloadScanReport,goSlowDetail,goExplainFromSlow,loadScanTasks,onTaskSelectChange,deleteScanTask,batchDeleteScanTasks,startScanTask,viewTaskSlowQueries,clearOrphanRecords,analyzeExplainBySql,analyzeExplain,loadSavedConnections,testConn,saveConn,openEditConn,openNewConn,deleteConn,setDefaultConn,connectInstance,loadUsers,createUser,openResetPwd,resetUserPwd,unlockUser,toggleUserStatus,deleteUser,loadAll,renderTrendChart,loadProjects,loadActiveAlerts,loadScanSchedules,createScanSchedule,deleteScanSchedule,toggleScheduleEnabled,runHealthCheck,runSchemaCheck,exportSchemaCheckReport,loadBigtable,bigtableRowKey,partitionBoundaryLabel,bigtableRowClass,togglePartitions,onBigtableExpand,loadTablePartitions,loadProjectsList,createProject,deleteProject,toggleProjectStatus,loadRulesets,loadGateRules,loadGateStrategies,applyGateStrategy,loadMonitorAlerts,acknowledgeAlert,loadMonitorRules,loadInspectionTasks,loadAuditLogs,loadRetention,runRetentionCleanup,loadSysInfo,bigtableCollecting,collectBigtable,rulesetDialog,createRuleset,deleteRuleset,gateCustom,openGateCustom,saveGateCustom,monitorRuleDialog,createMonitorRule,inspectionDialog,createInspection,inspectionResultDrawer,inspectionResults,viewInspectionResult,retentionDialog,openRetentionEdit,saveRetention,retentionEditMode,logoUrl,loadLogo,onLogoUpload,resetLogo,toggleSysConfig,auditFilter,resetAuditFilter,tableNameLabel,metricLabel,rolesList,rolesLoading,roleDialog,deleteRole,openRoleEdit,saveRole,roleLabelFn,permsMatrixData,permsMenuList,permsLoading,loadPerms,onPermChange,deepConnId,deepRightConnId,deepDb,deepTab,deepLoading,deepResult,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,visibleMenus,zkDialogVisible,zkForm,zkScanning,zkDiscovered,zkSelected,zkRegistering,openZkDiscovery,runZkDiscovery,handleZkSelection,registerZkInstances,gatewayLoading,gatewayReports,gatewayHtml,gatewayDetailVisible,loadGatewayReports,viewGatewayReport,onGatewayUpload,pptLoading,pptDashboard,loadPptDashboard,generatePptReport,toolkitLoading,toolkitScripts,loadToolkitScripts,downloadToolkitScript,extractedTab,extractedReports,extractedReportsLoading,loadExtractedReports,downloadExtractedHtmlReport,downloadExtractedSqlFile,dailyInspectDates,dailyInspectThreshold,dailyCompareResult,dailyInstSearch,dailyInstSigOnly,dailySrvSearch,dailySrvSigOnly,dailyInspectChartData,dailyInspectChartMetric,dailyInspectChartNode,dailyInspectChartNodes,dailyTrendChartRef,filteredDailyInstDiffs,filteredDailySrvDiffs,runDailyInspect,compareDailyInspect,renderDailyTrendChart,exportDailyHtmlReport,activeEmergencyNames,emergencyNameLabel,rulesetDrawer,rulesetConfigItems,openRulesetConfig,rulesetCategories,rulesetCategoryCounts,filteredRulesetItems,modifiedOverrideCount,disabledCount,setFilteredRulesEnabled,resetFilteredRulesOverrides,saveRulesetConfig};
  }
});
app.use(ElementPlus,{locale:ElementPlusLocaleZhCn});
// 确保图标组件（含主题开关用的 Sunny/Moon）全局可用
try{for(const[k,c]of Object.entries(ElementPlusIconsVue||{}))app.component(k,c)}catch(e){}
app.mount('#app');
