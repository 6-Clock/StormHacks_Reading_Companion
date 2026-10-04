// Run with gstack browse on localhost, then reload. All API calls here are fixtures.
return (async () => {
  const assert = (value, label) => { if (!value) throw new Error(label); };
  const waitFor = async (check, label) => {
    const end = Date.now() + 7000;
    while (!check()) {
      if (Date.now() > end) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 40));
    }
  };
  const button = label => [...document.querySelectorAll('button')].find(el => el.textContent.trim() === label);
  const picture = () => document.querySelector('img[alt="Live OCR camera 2"]');
  const canvas = document.createElement('canvas'); canvas.width = 8; canvas.height = 8;
  const jpeg = color => { canvas.getContext('2d').fillStyle = color; canvas.getContext('2d').fillRect(0, 0, 8, 8); return canvas.toDataURL('image/jpeg'); };
  let frameImage = jpeg('#aa2222');
  let frozen = false;
  let freezeTime = 0;
  let starts = 0;
  let previews = 0;
  let job = {job_id:null, status:'idle', events:[]};
  const originalFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (!path.startsWith('/v1/')) return originalFetch(input, init);
    let body;
    if (path === '/v1/tracker-settings') body = {blink_only:false,revision:'one',applied_revision:null,tracker_connected:false};
    else if (path === '/v1/diagnostics/eyes') body = {connected:false,events:[]};
    else if (path === '/v1/scan-jobs/latest') body = job;
    else if (path === '/v1/scan-jobs' && init.method === 'POST') {
      starts++;
      job = {job_id:'live-test',source:'manual',camera_index:2,status:'opening_camera',message:'Opening camera 2.',events:[]};
      body = job;
    } else if (path.endsWith('/preview')) {
      previews++;
      body = {...job,frame:job.status === 'framing' ? {data_url:frameImage,width:8,height:8,captured_at:frozen ? freezeTime : Date.now()/1000} : null};
    } else if (path.endsWith('/capture')) {
      job = {...job,status:'transcribing',message:'Reading text'}; body = job;
    } else if (path.endsWith('/cancel')) {
      job = {...job,status:'cancelled',message:'Camera released'}; body = job;
    } else if (path === '/v1/scan-jobs/live-test') body = job;
    else throw new Error(`Unexpected QA API call: ${path}`);
    return new Response(JSON.stringify(body), {status:200,headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => document.querySelector('[aria-label="Open Developer view"]'), 'reader');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => button('Live camera') && !button('Live camera').disabled, 'preview control');
  button('Live camera').click();
  await waitFor(() => document.querySelector('[aria-label="Live book camera"]'), 'opening preview');
  assert(!picture(), 'opening must not invent a frame');
  job = {...job,status:'framing',message:'Frame the page'};
  await waitFor(() => picture(), 'real frame response displayed');
  assert(button('Live camera').disabled && button('Test 3 blinks').disabled, 'preview reserves scanner');
  const first = picture().src;
  frameImage = jpeg('#2255aa');
  await waitFor(() => picture()?.src !== first, 'successive frames update');
  frozen = true; freezeTime = Date.now()/1000;
  await waitFor(() => !picture(), 'stale frames disappear');
  frozen = false;
  await waitFor(() => picture(), 'fresh camera recovers');
  document.querySelector('[aria-label="Return to Reader view"]').click();
  await waitFor(() => !picture(), 'Reader hides live preview');
  const beforeHidden = previews;
  await new Promise(resolve => setTimeout(resolve, 650));
  assert(previews === beforeHidden, 'hidden Developer view must stop preview polling');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => picture(), 'Developer resumes preview');
  button('Capture now').click();
  await waitFor(() => !document.querySelector('[aria-label="Live book camera"]'), 'capture clears live image');
  assert(starts === 1, 'capture reuses the preview job');
  assert(document.body.innerText.includes('Reading text'), 'capture starts OCR');
  button('Cancel scan').click();
  await waitFor(() => !button('Live camera').disabled, 'cancel releases controls');
  return {passed:['opening without fake frames','live frames update','preview reserves scanner','stale frames clear','fresh recovery','hidden preview suspends polling','capture reuses camera job','cancel releases controls'],starts,previews};
})()
