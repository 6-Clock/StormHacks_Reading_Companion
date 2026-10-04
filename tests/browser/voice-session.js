// Evaluate on a fresh local reader page. RTC, microphone and all API calls are mocks.
return (async () => {
  const assert = (condition, label) => { if (!condition) throw new Error(label); };
  const waitFor = async (check, label) => {
    const until = Date.now() + 9000;
    while (!check()) {
      if (Date.now() > until) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 40));
    }
  };
  const button = label => [...document.querySelectorAll('button')].find(el => el.textContent.trim() === label);
  const byLabel = label => document.querySelector(`button[aria-label="${label}"]`);
  const fixture = {peers:[],tracks:[],audio:[],calls:[],denied:false,jobs:{},job:{job_id:null,status:'idle',events:[]},sequence:0};
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 8;
  const capture = () => ({data_url:canvas.toDataURL('image/jpeg'),width:8,height:8,captured_at:Date.now()/1000,page_detected:true});
  const backend = (peer, delegation_id, event, client_event_id) => peer.channel.emit({type:'response.event',delegation_id,event,client_event_id});
  class Channel extends EventTarget {
    readyState = 'open';
    sent = [];
    send(data) { this.sent.push(JSON.parse(data)); }
    emit(event) { this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(event)})); }
    close() { this.readyState = 'closed'; this.dispatchEvent(new Event('close')); }
  }
  class Peer extends EventTarget {
    iceGatheringState = 'complete';
    connectionState = 'new';
    sctp = {maxMessageSize:65536};
    channel = new Channel();
    constructor() { super(); fixture.peers.push(this); }
    createDataChannel(name) { assert(name === 'oai-events', 'uses Live data channel'); return this.channel; }
    addTrack() {}
    async createOffer() { return {type:'offer',sdp:'mock_offer'}; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription(value) {
      assert(value.sdp === 'mock_answer','applies backend SDP answer');
      this.connectionState = 'connected';
      setTimeout(() => this.channel.emit({type:'session.started',session:{id:'live_browser_test'}}),20);
    }
    close() { this.connectionState = 'closed'; }
  }
  window.RTCPeerConnection = Peer;
  window.Audio = class {
    muted = false;
    paused = false;
    constructor() { fixture.audio.push(this); }
    async play() { this.paused = false; }
    pause() { this.paused = true; }
  };
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async () => {
    if (fixture.denied) throw new DOMException('Microphone permission denied','NotAllowedError');
    const track = {enabled:true,stopped:false,stop() { this.stopped = true; }};
    fixture.tracks.push(track);
    return {getTracks:() => [track],getAudioTracks:() => [track]};
  }});
  const originalFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url,location.href).pathname;
    if (!path.startsWith('/v1/')) return originalFetch(input,init);
    fixture.calls.push({path,method:init.method || 'GET',body:init.body});
    let body;
    if (path === '/v1/tracker-settings') body = {blink_only:false,revision:'fixture',applied_revision:'fixture',tracker_connected:false};
    else if (path === '/v1/diagnostics/eyes') body = {connected:false,updated_at:null,events:[]};
    else if (path === '/v1/scan-jobs/latest') { body = {...fixture.job}; delete body.result; }
    else if (path === '/v1/voice/session') body = {session:{id:'live_browser_test'},transport:{type:'webrtc',sdp:'mock_answer'}};
    else if (path === '/v1/voice/images') body = {file_id:'file_browser_image'};
    else if (path === '/v1/scan-jobs' && init.method === 'POST') {
      const request = JSON.parse(init.body);
      fixture.job = {job_id:`voice-scan-${++fixture.sequence}`,trigger_id:request.trigger_id,source:request.source,camera_index:request.camera_index,status:request.source === 'manual' ? 'framing' : 'captured',message:'Capture ready',events:[]};
      if (request.source !== 'manual') fixture.job.result = capture();
      fixture.jobs[fixture.job.job_id] = fixture.job;
      body = fixture.job;
    } else if (path.endsWith('/preview')) body = {...fixture.job,frame:null};
    else if (path.endsWith('/capture')) {
      fixture.job.status = 'captured'; fixture.job.result = capture(); body = fixture.job;
    } else if (path.endsWith('/cancel')) {
      const id = path.split('/').at(-2);
      fixture.jobs[id].status = 'cancelled'; body = fixture.jobs[id];
    } else if (path.endsWith('/result')) {
      const id = path.split('/').at(-2);
      const result = JSON.parse(init.body);
      Object.assign(fixture.jobs[id],{status:result.accepted ? 'accepted' : 'rejected',result:{...fixture.jobs[id].result,...result}});
      body = fixture.jobs[id];
    } else if (path.startsWith('/v1/scan-jobs/')) body = fixture.jobs[path.split('/').at(-1)];
    else throw new Error(`Unexpected API call: ${path}`);
    return new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => button('Start conversation'), 'reader voice controls');
  button('Start conversation').click();
  await waitFor(() => button('End conversation') && !button('End conversation').disabled, 'Live session starts');
  const peer = fixture.peers.at(-1);
  const track = fixture.tracks.at(-1);
  assert(fixture.calls.filter(call => call.path === '/v1/voice/session').length === 1, 'one session bootstrap');
  const session = JSON.parse(fixture.calls.find(call => call.path === '/v1/voice/session').body);
  assert(session.sdp === 'mock_offer' && session.page_text.includes('Mara'), 'session includes offer and current page');
  byLabel('Mute microphone').click();
  await waitFor(() => !track.enabled, 'microphone muted');
  assert(peer.connectionState === 'connected' && !fixture.audio.at(-1).muted, 'mute preserves session and playback');
  byLabel('Unmute microphone').click();
  await waitFor(() => track.enabled, 'microphone unmuted');
  const input = document.querySelector('input[aria-label="Question about the page"]');
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  setValue.call(input,'Why was Mara afraid?');
  input.dispatchEvent(new Event('input',{bubbles:true}));
  await waitFor(() => byLabel('Send question') && !byLabel('Send question').disabled,'typed question ready');
  byLabel('Send question').click();
  await waitFor(() => peer.channel.sent.some(event => event.type === 'response.create'),'typed question delegated');
  assert(peer.channel.sent.some(event => event.type === 'response.item.create' && JSON.stringify(event.item).includes('Why was Mara afraid?')),'typed text uses the same session');
  peer.channel.emit({type:'session.output_transcript.delta',event_id:'out_1',delta:'She heard footsteps.',start_ms:100,end_ms:200});
  peer.channel.emit({type:'session.input_transcript.delta',event_id:'in_1',delta:'What was upstairs?',start_ms:300,end_ms:400});
  await waitFor(() => document.body.innerText.includes('She heard footsteps.') && document.body.innerText.includes('What was upstairs?'),'native transcript journal');
  byLabel('Stop audio').click();
  await waitFor(() => peer.channel.sent.some(event => event.type === 'session.close'),'stop ends session');
  assert(fixture.audio.at(-1).muted && fixture.audio.at(-1).paused && !track.enabled,'stop suppresses local audio immediately');
  assert(peer.connectionState !== 'closed','transport remains until finalization');
  peer.channel.emit({type:'session.closed'});
  await waitFor(() => button('Start conversation') && !button('Start conversation').disabled,'session finalizes');
  assert(track.stopped && peer.connectionState === 'closed','finalization releases microphone and peer');
  button('Start conversation').click();
  await waitFor(() => button('End conversation') && !button('End conversation').disabled,'second session starts');
  const scanPeer = fixture.peers.at(-1);
  const previousPage = document.querySelector('[aria-label="Story text"]').textContent;
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled,'manual capture ready');
  button('Scan OCR').click();
  await waitFor(() => button('Capture now'),'manual framing');
  button('Capture now').click();
  await waitFor(() => scanPeer.channel.sent.some(event => JSON.stringify(event).includes('voice-scan-1') && JSON.stringify(event).includes('input_image')),'JPEG sent through Live');
  const scanCommand = scanPeer.channel.sent.findLast(event => event.type === 'response.create');
  backend(scanPeer,'bad_scan',{type:'response.output_text.done',item_id:'bad',text:'{"task":"scan_page","job_id":"voice-scan-1","accepted":true}'},scanCommand.event_id);
  backend(scanPeer,'bad_scan',{type:'response.completed',response:{output:[]}},scanCommand.event_id);
  await waitFor(() => document.body.innerText.includes('invalid scan result'),'malformed scan reported');
  assert(document.querySelector('[aria-label="Story text"]').textContent === previousPage,'malformed OCR keeps previous page');
  assert(button('Retry processing') && !button('Retry processing').disabled && button('Cancel processing') && !button('Cancel processing').disabled,'malformed OCR retains usable recovery controls');
  button('Retry processing').click();
  await waitFor(() => scanPeer.channel.sent.filter(event => event.type === 'response.create').length >= 2,'retry reuses capture');
  assert(fixture.calls.filter(call => call.path === '/v1/scan-jobs' && call.method === 'POST').length === 1,'retry does not acquire another image');
  scanPeer.sctp.maxMessageSize = 512;
  const callsBeforeTool = scanPeer.channel.sent.length;
  backend(scanPeer,'recapture',{type:'response.output_item.done',item:{type:'function_call',call_id:'capture_again',name:'capture_page',arguments:'{}'}});
  backend(scanPeer,'recapture',{type:'response.completed',response:{output:[]}});
  await waitFor(() => scanPeer.channel.sent.slice(callsBeforeTool).some(event => event.type === 'response.create'),'capture tool finishes before continuation');
  const toolEvents = scanPeer.channel.sent.slice(callsBeforeTool);
  const imageIndex = toolEvents.findIndex(event => JSON.stringify(event).includes('file_browser_image'));
  const outputIndex = toolEvents.findIndex(event => event.item?.type === 'function_call_output');
  const continuationIndex = toolEvents.findIndex(event => event.type === 'response.create');
  assert(imageIndex >= 0 && outputIndex > imageIndex && continuationIndex > outputIndex,'continuation follows image and truthful tool output');
  assert(fixture.calls.some(call => call.path === '/v1/voice/images'),'oversized JPEG uploaded to Files instead of data channel');
  assert(fixture.calls.some(call => call.path === '/v1/scan-jobs/voice-scan-1/cancel'),'recapture supersedes prior capture');
  const text = JSON.stringify({task:'scan_page',job_id:'voice-scan-2',accepted:true,text:'A new page from the captured book.',reason:'readable'});
  backend(scanPeer,'recapture_result',{type:'response.output_text.done',item_id:'ocr',text});
  backend(scanPeer,'recapture_result',{type:'response.completed',response:{output:[]}});
  await waitFor(() => document.querySelector('[aria-label="Story text"]').textContent.includes('A new page from the captured book.'),'validated OCR publishes new page');
  assert(fixture.calls.some(call => call.path === '/v1/scan-jobs/voice-scan-2/result'),'accepted result saved to current capture');
  assert(!scanPeer.channel.sent.some(event => event.type === 'session.close') && scanPeer.connectionState === 'connected','page acceptance keeps the same voice session');
  await waitFor(() => scanPeer.channel.sent.some(event => JSON.stringify(event).includes('page_context') && JSON.stringify(event).includes('A new page from the captured book.')),'new page context reaches ongoing session');
  fixture.job = {job_id:'voice-scan-3',source:'automatic',camera_index:2,status:'captured',message:'Automatic capture ready',events:[],result:capture()};
  fixture.jobs[fixture.job.job_id] = fixture.job;
  await waitFor(() => scanPeer.channel.sent.some(event => JSON.stringify(event).includes('voice-scan-3') && JSON.stringify(event).includes('input_image')),'automatic capture uses ongoing session');
  const rejected = JSON.stringify({task:'scan_page',job_id:'voice-scan-3',accepted:false,text:'',reason:'Page is blurred'});
  backend(scanPeer,'reject_result',{type:'response.output_text.done',item_id:'reject',text:rejected});
  backend(scanPeer,'reject_result',{type:'response.completed',response:{output:[]}});
  await waitFor(() => fixture.job.status === 'rejected','unreadable page rejection saved');
  assert(document.querySelector('[aria-label="Story text"]').textContent.includes('A new page from the captured book.'),'rejection preserves accepted page');
  assert(fixture.peers.length === 2 && scanPeer.connectionState === 'connected','automatic scan does not bootstrap another session');
  button('End conversation').click();
  await waitFor(() => scanPeer.channel.sent.some(event => event.type === 'session.close'),'explicit End closes conversation');
  scanPeer.channel.emit({type:'session.closed'});
  await waitFor(() => button('Start conversation') && !button('Start conversation').disabled,'explicit End finalizes');
  document.querySelector('[aria-label="Return to Reader view"]').click();
  fixture.denied = true;
  button('Start conversation').click();
  await waitFor(() => document.body.innerText.includes('Microphone permission denied'),'microphone error displayed');
  assert(fixture.peers.at(-1).connectionState === 'closed','microphone denial closes peer');
  assert(!fixture.calls.some(call => /speech|transcribe|narration|\/ask$/.test(call.path)),'no old speech pipeline');
  return {passed:['single session bootstrap','current page context','mic mute independent of playback','typed questions through Live','native transcript journal','stop suppresses audio immediately','session finalization cleanup','malformed OCR keeps page and recovery','retry reuses capture','model recapture sequencing','oversized JPEG upload','validated OCR publishes page','page updates preserve ongoing session','automatic capture through same session','rejection preserves accepted page','microphone denial cleanup']};
})()
