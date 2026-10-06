/* Run against a served snapshot. No hidden locator auto-scroll: every swipe counted. */
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const url = process.env.UAT_URL || 'http://127.0.0.1:8000';
const output = process.argv[2] || 'tmp/mobile-uat.json';
const compare = process.argv[3];

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const rows = [], errors = [];
  try {
    const registry = await (await fetch(`${url}/data/football/index.json`)).json();
    const shared = await (await fetch(`${url}/data/football/nfl.json`)).json();
    for (const width of [375,390]) {
      for (const entry of registry.leagues.filter(l => l.path)) {
        const data = await (await fetch(`${url}/${entry.path}`)).json();
        const team = data.league.teams.find(t => t.id === data.league.my_team) || data.league.teams[0];
        const analysis = data.analysis.teams[team.id];
        const tasks = [
          ['lineup', 'today', p => p.getByRole('heading',{name:'Lineup checks',exact:true})],
          ['last-alert', 'today', p => p.locator('#panel .card').filter({hasText: shared.players[analysis.alerts.at(-1)?.player_id]?.name || 'No injury or suspension'}).last()],
          ['deadline', 'today', p => p.getByRole('heading',{name:'Next decisions',exact:true})],
          ['first-pick', 'waivers', p => p.locator('#picks [data-player-id]').first()],
          ['last-pick', 'waivers', p => p.locator(`#picks [data-player-id="${analysis.waivers.at(-1)?.player_id}"]`)],
          ['find-pick', 'waivers', p => p.locator(`#picks [data-player-id="${analysis.waivers.at(-1)?.player_id}"]`)],
          ['last-roster', 'roster', p => p.locator('#panel .grid .card').last()],
          ['last-news', 'news', p => p.locator('#panel .list li').last()],
          ['confirm-cutoff', 'settings', p => p.locator('#deadline-form')],
          ['deep-list-to-roster', 'waivers', p => p.locator('#panel .grid .card').first()],
        ];
        for (const [task,view,target] of tasks) {
          if (['last-pick','find-pick','deep-list-to-roster'].includes(task) && !analysis.waivers.length) continue;
          const context = await browser.newContext({viewport:{width,height:812},isMobile:true,hasTouch:true,timezoneId:'America/New_York'});
          await context.addInitScript(({league,team}) => {localStorage.setItem('fgm.league.nfl',league);localStorage.setItem('fgm.team.'+league,team);}, {league:entry.id,team:team.id});
          const p = await context.newPage();
          p.on('pageerror',e => errors.push(e.message));
          await p.goto(url); await p.locator('#connected').waitFor({state:'visible'});
          await p.getByRole('heading',{name:/Today/}).waitFor();
          const row = {width,league:entry.id,task,taps:0,textEntries:0,swipes:0,scrollPixels:0};
          async function visible(locator) {
            if (!await locator.isVisible()) return false;
            const b = await locator.boundingBox();
            if (await locator.evaluate(el => !!el.closest('#views'))) return b.y >= 0 && b.y+b.height <= 812;
            const bottom = await p.locator('#views').evaluate(el => getComputedStyle(el).position === 'fixed' ? el.getBoundingClientRect().top : innerHeight);
            const top = await p.locator('#views').evaluate(el => getComputedStyle(el).position === 'sticky' && el.getBoundingClientRect().top <= 1 ? el.getBoundingClientRect().bottom : 0);
            return b.y >= top && b.y + Math.min(b.height,300) <= bottom;
          }
          async function reach(locator) {
            for (let i=0; !await visible(locator); i++) {
              assert(i<80, `Cannot reach ${entry.id}/${task}`);
              const b = await locator.boundingBox(); assert(b, `Hidden target ${task}`);
              const before = await p.evaluate(() => scrollY);
              const top = await p.locator('#views').evaluate(el => getComputedStyle(el).position === 'sticky' && el.getBoundingClientRect().top <= 1 ? el.getBoundingClientRect().bottom+8 : 8);
              await p.mouse.wheel(0, Math.max(-609,Math.min(609,b.y-top)));
              await p.waitForTimeout(60);
              const after = await p.evaluate(() => scrollY);
              row.swipes++; row.scrollPixels += Math.abs(after-before);
              assert(after!==before, `Scroll stuck ${task}`);
            }
          }
          async function tap(locator) {await reach(locator); await locator.tap(); row.taps++;}
          if (view !== 'today') await tap(p.locator(`#views [data-view="${view}"]`));
          const jump = p.locator(`[data-jump="${task === 'last-alert' ? 'alerts' : task === 'deadline' ? 'decisions' : 'lineup'}"]`);
          if (view==='today' && await jump.count()) await tap(jump);
          if (task==='find-pick') {
            const input=p.locator('#search'); await tap(input);
            await input.pressSequentially(shared.players[analysis.waivers.at(-1).player_id].name,{delay:10}); row.textEntries++;
            assert.equal(await input.inputValue(), shared.players[analysis.waivers.at(-1).player_id].name);
          }
          if (task==='last-pick' || task==='deep-list-to-roster' || task==='last-news') {
            const last = task==='last-news' ? p.locator('#panel .list li').last() : p.locator(`#picks [data-player-id="${analysis.waivers.at(-1).player_id}"]`);
            while (!await last.isVisible() && await p.locator('#next-page:not([disabled])').count()) await tap(p.locator('#next-page'));
            await reach(last);
          }
          if (task==='deep-list-to-roster') await tap(p.locator('#views [data-view="roster"]'));
          await reach(target(p));
          assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow ${task}`);
          row.interactions=row.taps+row.textEntries+row.swipes;
          rows.push(row);
          if (width===375 && entry===registry.leagues[0] && ['lineup','last-pick'].includes(task)) {
            fs.mkdirSync('tmp/uat-screenshots',{recursive:true}); await p.screenshot({path:`tmp/uat-screenshots/${task}-${compare?'after':'before'}.png`});
          }
          await context.close();
        }
      }
    }
    assert.equal(errors.length,0,errors.join('\n'));
    const totals=rows.map(r=>r.interactions).sort((a,b)=>a-b);
    const summary={journeys:rows.length,median:totals[Math.ceil(totals.length*.5)-1],p95:totals[Math.ceil(totals.length*.95)-1],max:totals.at(-1),swipes:rows.reduce((s,r)=>s+r.swipes,0)};
    const report={url,measuredAt:new Date().toISOString(),snapshotAt:registry.built_at,method:'Fresh page per task, saved own team; tap=1, text entry=1, swipe=609px (75% of 812px); target first 300px unobscured. Native dropdown selection excluded. Equal task weighting; not user telemetry.',summary,rows};
    if (compare) {
      const before=JSON.parse(fs.readFileSync(compare));
      assert.equal(before.snapshotAt, report.snapshotAt,'Snapshot changed; comparison invalid');
      assert.deepEqual(before.rows.map(r=>[r.width,r.league,r.task]),rows.map(r=>[r.width,r.league,r.task]),'Journey set changed');
      report.before=before.summary;
      assert(summary.p95 < before.summary.p95,'p95 did not improve');
      assert(summary.max < before.summary.max,'Worst path did not improve');
      assert(summary.swipes < before.summary.swipes,'Scrolling did not improve');
    }
    fs.mkdirSync(require('node:path').dirname(output),{recursive:true}); fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({output,summary,before:report.before},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
