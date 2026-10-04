// Evaluate on a fresh local reader page, then reload to restore the real API.
// Every /v1 request is mocked; this test cannot move hardware or call a provider.
return (async () => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const waitFor = async (check, label) => {
    const until = Date.now() + 9000;
    while (!check()) {
      if (Date.now() > until) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  };
  const button = label => [...document.querySelectorAll('button')].find(el => el.textContent.trim() === label);
  const story = () => document.querySelector('[aria-label="Story text"]')?.textContent;
  const fixture = { job: { job_id:null, status:'idle', message:'Ready to scan', events:[] }, calls:[], sequence:0 };
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 8;
  const capture = () => ({ data_url:canvas.toDataURL('image/jpeg'), width:8, height:8, captured_at:Date.now()/1000, page_detected:true });
  const originalFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (!path.startsWith('/v1/')) return originalFetch(input, init);
    const request = init.body ? JSON.parse(init.body) : null;
    fixture.calls.push({method:init.method || 'GET', path, request});
    let body;
    if (path === '/v1/tracker-settings') body = {blink_only:false, revision:'fixture', applied_revision:'fixture', applied_blink_only:false, tracker_connected:false};
    else if (path === '/v1/diagnostics/eyes') body = {connected:false, updated_at:null, events:[]};
    else if (path === '/v1/scan-jobs/latest') { body = {...fixture.job}; delete body.result; }
    else if (path === '/v1/scan-jobs' && init.method === 'POST') {
      fixture.job = {job_id:`scan-${++fixture.sequence}`, trigger_id:request.trigger_id, source:request.source, camera_index:request.camera_index, status:request.source === 'manual' ? 'framing' : 'captured', message:'Capture ready', events:[]};
      if (request.source !== 'manual') fixture.job.result = capture();
      body = fixture.job;
    } else if (path.endsWith('/preview')) body = {...fixture.job, frame:null};
    else if (path.endsWith('/capture')) { fixture.job = {...fixture.job, status:'captured', result:capture(), message:'Page captured'}; body = fixture.job; }
    else if (path.endsWith('/cancel')) { fixture.job = {...fixture.job, status:'cancelled', message:'Camera released'}; body = fixture.job; }
    else if (path === `/v1/scan-jobs/${fixture.job.job_id}`) body = fixture.job;
    else throw new Error(`Unexpected API call: ${path}`);
    return new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => document.querySelector('[aria-label="Open Developer view"]'), 'reader hydration');
  const previousPage = story();
  assert(button('Start conversation'), 'explicit conversation start exists');
  assert(!document.querySelector('.word-note, .asked-word, .scan-word-overlay'), 'word highlights are removed');
  assert(!document.body.innerText.includes('Immersive'), 'immersive controls are removed');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'scan connection');
  button('Scan OCR').click();
  await waitFor(() => button('Capture now'), 'manual framing');
  button('Cancel scan').click();
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'cancel releases camera controls');
  assert(fixture.calls.some(call => call.path.endsWith('/cancel')), 'cancel reaches the capture job');
  button('Scan OCR').click();
  await waitFor(() => button('Capture now'), 'next capture starts');
  button('Capture now').click();
  await waitFor(() => fixture.job.status === 'captured' && !button('Capture now'), 'capture completes without OCR pipeline');
  await waitFor(() => document.body.innerText.includes('Start conversation'), 'offline capture instructions');
  assert(story() === previousPage, 'offline capture preserves the accepted page');
  assert(!fixture.calls.some(call => /voice|speech|transcribe|ask|narration/.test(call.path)), 'capture makes no model or separate speech request');
  await waitFor(() => button('Test 3 blinks') && !button('Test 3 blinks').disabled, 'test capture available');
  const startedAt = Date.now();
  button('Test 3 blinks').click();
  await waitFor(() => fixture.calls.some(call => call.path === '/v1/scan-jobs' && call.request.source === 'test'), 'blink simulation starts capture');
  const request = fixture.calls.findLast(call => call.path === '/v1/scan-jobs').request;
  assert(!('settle_seconds' in request), 'blink capture has no fixed settling parameter');
  assert(Date.now() - startedAt < 6000, 'blink simulation does not add an eight-second wait');
  assert(!document.querySelector('[aria-label="Page-turn wait"]'), 'settling countdown is removed');
  return {passed:['explicit conversation start','no highlights or immersive controls','manual cancel and restart','offline capture keeps page','capture-only requests','blink capture without settling'], calls:fixture.calls.length};
})()
