import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const input=[];for await(const chunk of process.stdin)input.push(chunk);
const {base}=JSON.parse(Buffer.concat(input).toString());
const browser=await chromium.launch({headless:true,executablePath:process.env.AIHUB_BROWSER_PATH,args:['--no-sandbox','--disable-dev-shm-usage']});
const page=await browser.newPage({viewport:{width:390,height:844}});let chatRequests=0;
page.on('request',r=>{if(r.url().includes('/api/campus/chat/stream'))chatRequests++;});
try{
 await page.goto(base+'/login');await page.getByRole('button',{name:'Instant Student Demo Login'}).click();await page.waitForURL('**/account');
 await page.getByRole('textbox',{name:'Ask a question about IIT Indore'}).fill('Show admissions');await page.getByRole('button',{name:'Send question',exact:true}).click();
 await page.getByText('Simulated · not saved',{exact:false}).waitFor();assert.equal(chatRequests,0);
 await page.getByRole('textbox',{name:'Ask a question about IIT Indore'}).fill('Show hostel information');await page.getByRole('button',{name:'Send question',exact:true}).click();
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 try{await page.locator('.chat-error').filter({hasText:'Response stopped'}).waitFor({timeout:5000});}catch(e){console.log(JSON.stringify({errors:await page.locator('.chat-error').allTextContents(),answers:await page.locator('.answer-footer').allTextContents(),buttons:await page.locator('.composer-actions').innerText(),url:page.url()}));throw e;}
 assert.equal(await page.locator('.answer-card').count(),1);assert.equal(chatRequests,0);
 console.log('PASS: demo streams sample answers, stop removes incomplete sample, zero backend chat requests.');
}finally{await browser.close();}
