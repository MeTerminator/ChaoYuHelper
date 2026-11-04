/* Easemob adapter. Sending is only invoked by the explicit composer action. */
import SDK from 'easemob-websdk';
const REST_URL = 'https://a3-vip6.easemob.com';
const emit = (name, data) => {if (window.emitEvent) window.emitEvent(name, data);};
const error = e => {
  const message = String(e?.message || e?.data || e?.type || e);
  if (Number(e?.type ?? e?.code) === 214 || /the number of devices.*exceeds the limit/i.test(message))
    return {error:'消息服务登录设备数已达上限（214）。请先退出其他电脑、浏览器或旧版助手中的学习通消息会话，再重新连接；关闭页面后名额可能不会立即释放。'};
  return {error:message};
};
let connection;
let activeAccount;
let generation = 0;
let pending;
window.courseIM = {
  connect(account) {
    if (activeAccount === account.accountId) {
      if (pending) return pending;
      if (connection?.isOpened()) return Promise.resolve({ok:true});
    }
    const attempt = this.open(account);
    pending = attempt;
    const clear = () => {if(pending === attempt) pending = undefined;};
    attempt.then(clear, clear);
    return attempt;
  },
  async open(account) {
    const current = ++generation;
    let client;
    try {
      if (connection) connection.close();
      activeAccount = account.accountId;
      client = new SDK.connection({appKey:'cx-dev#cxstudy',apiUrl:REST_URL,https:true,
        isHttpDNS:true,autoReconnectNumMax:3,isFixedDeviceId:true});
      // Keep the SDK's Web osType=16, matching the current official Web IM.
      // Let the SDK persist a device identifier for this browser.
      connection = client;
      const status = value => {if(current===generation)emit('course-im-status',{status:value});};
      client.addEventHandler('course-helper', {
        onConnected:()=>status('消息服务已连接'),
        onDisconnected:()=>status('消息服务已断开'),
        ...Object.fromEntries(['onTextMessage','onCustomMessage','onCmdMessage','onImageMessage',
          'onFileMessage','onAudioMessage','onVideoMessage'].map(name=>[name,m=>{
            if(current===generation)this.receive(m,account.accountId);
          }])),
        onReadMessage:m=>{if(current===generation)emit('course-im-control',{...m,type:'read',_account_id:account.accountId});},
        onChannelMessage:m=>{if(current===generation)emit('course-im-control',{...m,type:'channel',_account_id:account.accountId});},
        onRecallMessage:m=>{if(current===generation)emit('course-im-control',{...m,type:'recall',_account_id:account.accountId});},
        onError:e=>status('消息服务错误：'+error(e).error)
      });
      await client.open({user:account.userName,accessToken:account.accessToken});
      // DNS discovery can replace apiUrl; the official client restores this REST host after login.
      client.apiUrl = REST_URL;
      if(current!==generation){client.close();return {error:'账号已切换'};}
      return {ok:true};
    } catch(e) {
      if(current===generation) {
        connection = undefined;
        activeAccount = undefined;
      }
      client?.close();
      return error(e);
    }
  },
  receive(message,accountId) {
    if(accountId!==activeAccount) return;
    if(message.type==='cmd' && !['CMD_MESSAGE_TIP','CMD_GROUP_CHAT_INVITATION'].includes(message.action)) {
      emit('course-im-control',{...message,_account_id:accountId});
      return;
    }
    emit('course-im-message',{...message,_account_id:accountId});
    let attachment=message.ext?.attachment || message.attributes?.attachment || {};
    if(typeof attachment==='string'){try{attachment=JSON.parse(attachment);}catch{attachment={};}}
    const activity=attachment.att_chat_course || attachment.att_sign;
    if(activity && 'Notification' in window && Notification.permission==='granted')
      new Notification('课堂活动',{body:activity.title || '收到新的课堂活动'});
  },
  status() {return {connected:!!connection?.isOpened(),deviceType:connection?.osType,accountId:activeAccount,userId:connection?.user};},
  close() {generation++;pending=undefined;activeAccount=undefined;if(connection){connection.close();connection=null;}},
  async groups() {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    try {
      let data=[];
      for(let pageNum=0;pageNum<100;pageNum++) {
        const response=await connection.getJoinedGroups({pageNum,pageSize:20,needAffiliations:true});
        const page=response.data || response.entities || [];data.push(...page);if(page.length<20)break;
      }
      return {data};
    } catch(e) {return error(e);}
  },
  async conversations() {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    const client=connection, current=generation;
    let data=[], cursor='';
    try {
      for(let page=0;page<100;page++) {
        const r=await client.getServerConversations({pageSize:50,cursor,includeEmptyConversations:true});
        if(current!==generation) return {error:'账号已切换'};
        data.push(...(r.conversations || r.data?.conversations || []));
        const next=r.cursor ?? r.data?.cursor;
        if(!next || next===cursor) return {data};
        cursor=next;
      }
      return {data,warning:'会话较多，请刷新以同步最新会话'};
    } catch(e) {return {...error(e),data};}
  },
  async historyPage(targetId,chatType='groupChat',cursor=-1) {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    if(!['groupChat','singleChat'].includes(chatType)) return {error:'不支持的会话类型'};
    const client=connection,current=generation;
    try {
      const r=await client.getHistoryMessages({targetId,chatType,pageSize:30,cursor,searchDirection:'up'});
      if(current!==generation) return {error:'账号已切换'};
      const next=r.cursor ?? r.data?.cursor;
      return {data:r.messages || r.data?.messages || [],cursor:next,
        isLast:!!(r.isLast ?? r.data?.isLast) || next==null || next===cursor};
    } catch(e) {return error(e);}
  },
  async sendText({accountId,targetId,chatType,text}) {
    if(accountId!==activeAccount) return {error:'账号已切换，请重新选择会话'};
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    if(!targetId || !['groupChat','singleChat'].includes(chatType)) return {error:'请选择有效会话'};
    if(typeof text!=='string' || !text.trim()) return {error:'消息不能为空'};
    if(new TextEncoder().encode(text).length>5000) return {error:'消息不能超过 5000 字节'};
    const client=connection,current=generation;
    try {
      const message=SDK.message.create({type:'txt',to:targetId,chatType,msg:text});
      const response=await client.send(message);
      // Never retry automatically: an interrupted response may already have reached the recipient.
      if(current!==generation) return {error:'账号已切换，请在原账号中确认发送结果'};
      return {data:{...message,id:response.serverMsgId || response.localMsgId || message.id,
        from:client.user,to:targetId,chatType,msg:text,time:Date.now(),_account_id:accountId}};
    } catch(e) {return error(e);}
  },
  async history(groupId) {
    if(!connection?.isOpened()) return {error:'请先连接消息服务'};
    try {
      let cursor=-1, data=[];
      for(let page=0;page<20;page++) {
        const r=await connection.getHistoryMessages({targetId:groupId,chatType:'groupChat',pageSize:50,cursor,searchDirection:'up'});
        const messages=r.messages || r.data?.messages || [];
        data.push(...messages);const next=r.cursor ?? r.data?.cursor;
        if(r.isLast || r.data?.isLast || next==null || next===cursor)break;
        cursor=next;
      }
      return {data};
    } catch(e) {return error(e);}
  }
};
window.addEventListener('pagehide',()=>window.courseIM.close());
