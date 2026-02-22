
import { useState, useEffect } from 'react';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA'};

function StatusRow({label, ok, detail}){
  return <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',
    padding:'8px 0',borderBottom:`1px solid ${C.border}`}}>
    <div style={{display:'flex',alignItems:'center',gap:8}}>
      <div style={{width:8,height:8,borderRadius:'50%',
        background:ok?C.green:C.red,
        boxShadow:ok?`0 0 6px ${C.green}`:undefined}}/>
      <span style={{fontSize:13,color:'#E4E4E7'}}>{label}</span>
    </div>
    <span style={{fontSize:11,color:C.muted}}>{detail}</span>
  </div>;
}

export default function SupportButton({token, user}){
  const [open,   setOpen]   = useState(false);
  const [status, setStatus] = useState(null);
  const [loading,setLoading]= useState(false);

  const loadStatus = async()=>{
    setLoading(true);
    try{
      const r = await fetch('/api/tags/system-status',{headers:auth(token)});
      setStatus(await r.json());
    }catch(e){}finally{setLoading(false);}
  };

  useEffect(()=>{
    if(open) loadStatus();
  },[open]);

  return <>
    {/* Floating button */}
    <button onClick={()=>setOpen(true)} style={{
      position:'fixed',bottom:24,right:24,zIndex:1000,
      width:48,height:48,borderRadius:'50%',
      background:C.blue,border:'none',
      boxShadow:'0 4px 20px rgba(46,134,171,0.5)',
      cursor:'pointer',display:'flex',alignItems:'center',
      justifyContent:'center',fontSize:20,
      transition:'transform 0.15s',
    }}
    onMouseEnter={e=>e.target.style.transform='scale(1.1)'}
    onMouseLeave={e=>e.target.style.transform='scale(1)'}>
      🛟
    </button>

    {/* Modal */}
    {open&&<div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.7)',
      display:'flex',alignItems:'flex-end',justifyContent:'flex-end',
      zIndex:2000,padding:24}}>
      <div style={{background:C.card,border:`2px solid ${C.blue}`,
        borderRadius:18,padding:24,width:380,maxHeight:'85vh',overflowY:'auto',
        boxShadow:'0 20px 60px rgba(0,0,0,0.5)'}}>

        {/* Header */}
        <div style={{display:'flex',alignItems:'center',
          justifyContent:'space-between',marginBottom:16}}>
          <div>
            <div style={{fontSize:16,fontWeight:800,color:'#E4E4E7'}}>🛟 Support</div>
            <div style={{fontSize:11,color:C.muted}}>Prosper RFID Platform</div>
          </div>
          <button onClick={()=>setOpen(false)} style={{background:'none',border:'none',
            color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
        </div>

        {/* Current user */}
        <div style={{background:C.dark,borderRadius:10,padding:'10px 14px',marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:6}}>Logged In As</div>
          <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>
            {user?.full_name||user?.username}
          </div>
          <div style={{fontSize:11,color:C.blue,fontWeight:600}}>{user?.role}</div>
        </div>

        {/* System status */}
        <div style={{marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:8,
            display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            System Status
            <button onClick={loadStatus} style={{background:'none',border:'none',
              color:C.blue,cursor:'pointer',fontSize:11}}>
              {loading?'⏳':'🔄 Refresh'}
            </button>
          </div>
          {status&&<>
            <StatusRow label="Database"
              ok={status.database?.status==='OK'}
              detail="PostgreSQL"/>
            <StatusRow label="Gateways"
              ok={status.gateways?.healthy===status.gateways?.total}
              detail={`${status.gateways?.healthy}/${status.gateways?.total} healthy`}/>
            <StatusRow label="BLE Tags"
              ok={status.tags?.active>0}
              detail={`${status.tags?.active} active now`}/>
            <StatusRow label="Today's Sessions"
              ok={status.sessions?.today>0}
              detail={`${status.sessions?.today} students`}/>
            <StatusRow label="Unacked Alerts"
              ok={status.alerts?.unacked===0}
              detail={status.alerts?.unacked===0?'All clear':`${status.alerts?.unacked} need attention`}/>
          </>}
          {!status&&!loading&&<div style={{color:C.muted,fontSize:12,textAlign:'center',
            padding:12}}>Click Refresh to load status</div>}
        </div>

        {/* Quick info */}
        <div style={{background:C.dark,borderRadius:10,padding:'10px 14px',marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:8}}>Platform Info</div>
          {[
            ['Server',   '192.168.5.63'],
            ['Version',  'v1.0.0-MVP'],
            ['Build',    new Date().toLocaleDateString()],
          ].map(([k,v])=><div key={k} style={{display:'flex',
            justifyContent:'space-between',padding:'3px 0'}}>
            <span style={{fontSize:12,color:C.muted}}>{k}</span>
            <span style={{fontSize:12,color:'#E4E4E7',fontFamily:'monospace'}}>{v}</span>
          </div>)}
        </div>

        {/* Contact */}
        <div style={{textAlign:'center',fontSize:11,color:'#4A5568'}}>
          Issues? Contact IT Admin<br/>
          <span style={{color:C.blue}}>admin@prosperacademy.edu</span>
        </div>
      </div>
    </div>}
  </>;
}
