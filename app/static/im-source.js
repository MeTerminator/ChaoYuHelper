/* Read-only Easemob client: receives messages and queries joined groups/history. */
import SDK from 'easemob-websdk';
const emit = (name, data) => {if (window.emitEvent) window.emitEvent(name, data);};
const error = e => ({error: String(e?.message || e?.data || e?.type || e)});
let connection;
let activeAccount;
let generation = 0;
window.courseIM = {
  async connect(account) {
    const current = ++generation;
    try {
      if (connection) connection.close();
      activeAccount = account.accountId;
      const client = new SDK.connection({appKey:'cx-dev#cxstudy',isHttpDNS:true,autoReconnectNumMax:3,
        deviceId:'course-helper-linux',isFixedDeviceId:true});
      // Flutter used OS_LINUX=2; Chaoxing may reject the SDK's OS_OTHER=16.
      // The pinned 4.24.0 SDK writes this field into Provision.osType at login.
      client.osType = 2;
      connection = client;
      const status = value => {if(current===generation)emit('course-im-status',{status:value});};
      client.addEventHandler('course-helper', {
        onConnected:()=>status('消息服务已连接'),
        onDisconnected:()=>status('消息服务已断开'),
        onTextMessage:m=>this.receive(m,account.accountId), onCustomMessage:m=>this.receive(m,account.accountId),
        onCmdMessage:m=>this.receive(m,account.accountId),
        onError:e=>status('消息服务错误：'+error(e).error)
      });
      await client.open({user:account.userName,pwd:account.password});
      if(current!==generation){client.close();return {error:'账号已切换'};}
      return {ok:true};
    } catch(e) { return error(e); }
  },
  receive(message,accountId) {
    if(accountId!==activeAccount) return;
    emit('course-im-message',{...message,_account_id:accountId});
    let attachment=message.ext?.attachment || message.attributes?.attachment || {};
    if(typeof attachment==='string'){try{attachment=JSON.parse(attachment);}catch{attachment={};}}
    const activity=attachment.att_chat_course || attachment.att_sign;
    if(activity && 'Notification' in window && Notification.permission==='granted')
      new Notification('课堂活动',{body:activity.title || '收到新的课堂活动'});
  },
  status() {return {connected:!!connection?.isOpened(),deviceType:connection?.osType,accountId:activeAccount};},
  close() {generation++;activeAccount=undefined;if(connection){connection.close();connection=null;}},
  async groups() {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    try {
      let data=[];
      for(let pageNum=0;pageNum<100;pageNum++) {
        const response=await connection.getJoinedGroups({pageNum,pageSize:20,needAffiliations:true});
        const page=response.data || [];data.push(...page);if(page.length<20)break;
      }
      return {data};
    } catch(e) {return error(e);}
  },
  async history(groupId) {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    try {
      let cursor, data=[];
      for(let page=0;page<20;page++) {
        const r=await connection.getHistoryMessages({targetId:groupId,chatType:'groupChat',pageSize:50,cursor});
        const messages=r.messages || r.data?.messages || [];
        data.push(...messages);const next=r.cursor || r.data?.cursor;
        if(!next || next===cursor || messages.length<50)break;
        cursor=next;
      }
      return {data};
    } catch(e) {return error(e);}
  }
};
window.addEventListener('pagehide',()=>window.courseIM.close());
