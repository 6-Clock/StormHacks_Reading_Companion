// Run using gstack: browse goto http://localhost:3000; browse eval tests/browser/eye-diagnostics.js.
// This page alone receives fixtures. No measurements are posted to the live tracker API.
// Reload after the test to restore real API requests.
return (async () => {
  const waitFor = async (check, label) => {
    const deadline = Date.now() + 9000;
    while (!check()) {
      if (Date.now() > deadline) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  };
  const assert = (condition, label) => { if (!condition) throw new Error(label); };
  let freeze = false;
  const diagnostic = {
    connected:true, updated_at:Date.now()/1000, camera_index:1, mode:'STOP',
    eyes_visible:true, gaze:{x:0.5,y:0.5}, openness:{left:1,right:1},
    phase:'OPEN', blink_count:0, look_progress:0, capture_fps:30, inference_fps:25,
    frame_age_ms:20, calibrated:false, turns_blocked:false, blink_only:true, events:[],
  };
  const originalFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    let body;
    if (path === '/v1/diagnostics/eyes') {
      if (!freeze) diagnostic.updated_at = Date.now()/1000;
      body = diagnostic;
    } else if (path === '/v1/tracker-settings' && (!init.method || init.method === 'GET')) {
      body = {blink_only:true,revision:'fixture',applied_revision:'fixture',applied_blink_only:true,tracker_connected:true,eye_camera_index:1};
    } else if (path === '/v1/scan-jobs/latest') {
      body = {job_id:null,trigger_id:null,status:'idle',message:'Ready to scan',events:[]};
    } else return originalFetch(input, init);
    return new Response(JSON.stringify(body), {status:200,headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => document.querySelector('[aria-label="Open Developer view"]'), 'reader hydrated');
  document.querySelector('[aria-label="Open Developer view"]').click();
  const status = () => document.querySelector('[aria-label="Camera blink detection"]')?.innerText || '';
  await waitFor(() => status().includes('Press C'), 'calibration prompt');
  assert(!status().includes('Ready'), 'uncalibrated input must not appear ready');
  diagnostic.calibrated = true;
  await waitFor(() => status().includes('Ready'), 'calibrated blink-only readiness');
  assert(!status().includes('Camera recorded'), 'no invented camera gesture');
  diagnostic.events = [{id:'fixture-blink-3',time:Date.now()/1000,type:'blink',message:'Blink 3/3 recorded (0.10s)'}];
  await waitFor(() => status().includes('Camera recorded 3 / 3'), 'completed camera event remains visible after counter reset');
  diagnostic.turns_blocked = true;
  await waitFor(() => status().includes('paused'), 'busy guard visible');
  diagnostic.turns_blocked = false;
  diagnostic.eyes_visible = false;
  await waitFor(() => status().includes('both eyes'), 'lost face visible');
  diagnostic.eyes_visible = true;
  await waitFor(() => status().includes('Ready'), 'face recovered');
  freeze = true;
  await waitFor(() => status().includes('Waiting for live eye'), 'frozen capture expires despite repeated successful polls');
  freeze = false;
  await waitFor(() => status().includes('Ready'), 'fresh capture restores live state');
  return {passed:['calibration readiness','real-event confirmation','busy and lost-face guards','frozen-frame expiry','fresh-frame recovery']};
})()
