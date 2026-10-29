import {icon,mountIcons} from '/static/utils/lucide-icons.mjs?v=20261026a';
export {icon};
const buttons={
 orbitBtn:['mouse-pointer-2','观察'],measureBtn:['ruler','测量'],floorBtn:['scan-line','可走区'],holeBtn:['box','障碍'],stairBtn:['route','楼梯'],doorBtn:['door-open',null],exitBtn:['log-out','出口'],spawnBtn:['map-pin','起始区'],finishBtn:['check-check','完成'],
 uploadBtn:['upload',null],editBtn:['mouse-pointer-2','编辑'],focusSelection:['focus',null],deleteSelection:['trash-2',null],deselectBtn:['x',null],recalcDoor:['compass',null],inspectorToggle:['sliders-horizontal',null],inspectorClose:['x',null],useCalibrationModel:['layers',null],resetDraftBtn:['rotate-ccw',null],confirmReview:['circle-check',null],undoBtn:['undo-2',null],reloadBtn:['refresh-cw',null],backBtn:['arrow-left',null],applyScale:['arrow-up-right',null],clearMeasure:['rotate-ccw',null],addFloor:['plus',null],updateFloor:['refresh-cw',null],removeFloor:['trash-2',null],saveBtn:['save',null],activateBtn:['arrow-up-right',null],previewNav:['route',null],viewHomeBtn:['maximize','全局视图'],previewSmoke:['wind',null],tokenSave:['shield-check',null],
 ackAnnotationRecheck:['scan-search',null],syncAnnotationScale:['scale',null],
 observeBtn:['eye','总览'],floorModeBtn:['layers','楼层'],replayBtn:['route','回放'],autoBtn:['orbit','旋转'],followBtn:['focus','跟随'],firstPersonToggle:['user','第一视角'],roofBtn:['box','屋顶'],clipBtn:['scan-line','剖切'],stairsBtn:['route','楼梯'],calibrateBtn:['ruler','校准'],
 refreshBtn:['refresh-cw',null],sceneReload:['refresh-cw',null],startBtn:['play',null],pauseBtn:['pause',null],resetBtn:['rotate-ccw',null],restartBtn:['rotate-ccw',null],fireBtn:['flame',null],fireToolBtn:['flame',null],followSelectedBtn:['focus',null],firstPersonBtn:['user',null],fpRecenterBtn:['compass',null],replayPlayBtn:['play',null],replayExitBtn:['x',null],rejoinBtn:['users','更换参与者'],exitButton:['log-out','退出'],padMode:['keyboard','方向键'],stickMode:['joystick','摇杆'],consentBtn:['arrow-right',null],completeRejoinBtn:['arrow-right',null]
};
const tooltips={
 floorCutaway:{title:'楼层剖切',description:'隐藏当前楼层上方的结构，从上向下核对室内边界、门洞与楼梯；再次点击恢复完整模型。',icon:'scan-line',kind:'toggle'},
 orbitBtn:{title:'观察与导航',description:'左键拖动旋转；右键或 Shift+左键拖动平移，滚轮缩放。可进入建筑、房间和楼梯附近核对，不会修改标注。',icon:'orbit',kind:'tool'},
 viewHomeBtn:{title:'恢复全局视图',description:'回到整栋建筑的初始观察角度和尺度。进入房间后按 Home 或点此按钮，快速找回整栋建筑与楼层位置。',icon:'maximize',kind:'action'},
 editBtn:{title:'选择与编辑',description:'在模型上选择已标注的可走区、门、楼梯、出口或生成区，再执行聚焦或删除。',icon:'move',kind:'tool'},
 measureBtn:{title:'长度测量',description:'在同一楼层拾取两个端点，读取模型尺寸；随后在参数面板填写实际长度并应用尺度。',icon:'ruler',kind:'tool'},
 floorBtn:{title:'绘制可走区',description:'沿可通行边界逐点点击，至少三个点后点“完成绘制”。范围将作为人物移动和导航的基础。',icon:'scan-line',kind:'tool'},
 holeBtn:{title:'标记障碍孔洞',description:'先点击所属可走区内部，再沿不可通行边界逐点圈定设备、柱体或家具占地。障碍孔洞会从人物可走范围中排除。',icon:'circle-stop',kind:'tool'},
 stairBtn:{title:'连接楼梯',description:'先选起止楼层与路径模式。直线楼梯点底端和顶端；折返或多段楼梯在底端后逐点点击踏面拐角，最后点击目标楼层内的顶端。底端和顶端都须位于各自可走区内。',icon:'route',kind:'tool'},
 doorBtn:{title:'创建门连接',description:'先在参数面板选择同层两个不同区域，再点击门中心。门中心须落在所选两侧之一，且不能落入第三个区域。',icon:'door-open',kind:'tool'},
 exitBtn:{title:'标记出口',description:'在当前楼层选择出口位置点击，出口会把可走区连接到建筑外部的撤离目标。',icon:'log-out',kind:'tool'},
 spawnBtn:{title:'绘制人物起始区',description:'圈定人物生成时出现的区域，整块轮廓须在同一可走区内并避开障碍。起始区只决定生成位置，不限制人物后续活动范围。',icon:'map-pin',kind:'tool'},
 finishBtn:{title:'完成当前绘制',description:'闭合当前可走区、障碍孔洞或起始区轮廓，至少需要三个点。楼梯不使用此按钮：点击目标楼层可走区内的顶端会自动完成路径。',icon:'check-check',action:'完成绘制'},
 'step:model':{title:'第 1 步 · 模型',description:'核对建筑模型与模型版本；默认 Web 视图用于流畅校准，移动端模型用于细节核对。',icon:'building-2',kind:'step'},
 'step:scale':{title:'第 2 步 · 比例',description:'在模型中量取一段已知长度，填写建筑实测米数；比例验证后，楼层高度与人物半径才会按真实尺度计算。',icon:'ruler',kind:'step'},
 'step:floor':{title:'第 3 步 · 楼层',description:'核对楼层高度，绘制可走区、障碍孔洞与人物起始范围。',icon:'layers',kind:'step'},
 'step:paths':{title:'第 4 步 · 通路',description:'连接楼梯、门与出口，并检查每个可走区到出口的完整路线。',icon:'route',kind:'step'},
 'step:validate':{title:'第 5 步 · 校验',description:'运行导航与烟雾预览并查看结构检查；确认所有区域可达并完成人工通路复核后，保存并激活场景。',icon:'circle-check',kind:'step'},
 backBtn:{title:'返回观察主机',description:'回到三维疏散观察台；当前校准草稿会先自动暂存。',icon:'arrow-left'},
 focusSelection:{title:'聚焦选中对象',description:'把相机移动到当前选中的区域、门、楼梯或出口，便于近距离检查。',icon:'focus'},
 deleteSelection:{title:'删除选中对象',description:'删除当前选中的标注元素，并同步清理关联连接；操作后可以撤销。',icon:'trash-2'},
 recalcDoor:{title:'重算门朝向',description:'依据门两侧区域中心重新计算门的前后方向，修正反向或倾斜的连接。',icon:'compass'},
 deselectBtn:{title:'取消当前选择',description:'清除选中状态并收起对象操作栏，不修改任何标注数据。',icon:'x'},
 inspectorToggle:{title:'参数面板',description:'展开或收起右侧校准参数面板；各绘制工具的具体设置会在这里显示。',icon:'sliders-horizontal',kind:'toggle'},
 inspectorClose:{title:'收起参数面板',description:'关闭右侧参数面板，让三维画布获得更大的操作空间。',icon:'panel-left-close'},
 tokenSave:{title:'验证管理令牌',description:'验证管理员身份并读取当前场景草稿；令牌只保存在当前浏览器。',icon:'shield-check'},
 uploadBtn:{title:'上传 GLB 模型',description:'上传单文件 GLB，并基于新模型创建独立的临时比例校准草稿。',icon:'upload'},
 reloadBtn:{title:'重新加载模型',description:'按当前设置重新请求模型资源，用于恢复加载失败或清理显示状态。',icon:'refresh-cw'},
 useCalibrationModel:{title:'切换校准视图',description:'默认 Web 视图绘制批次更少；需要核对局部网格细节时切换到移动端模型，完成后可切回 Web 视图。',icon:'layers',kind:'toggle'},
 resetDraftBtn:{title:'放弃未保存修改',description:'丢弃浏览器自动暂存与撤销记录，恢复服务器当前保存的版本；需要确认后执行。',icon:'rotate-ccw'},
  undoBtn:{title:'撤销上一步',description:'绘制中回退最后一个点、楼梯底端或测量点；完成标注后回退最近一项完整修改。快捷键 Ctrl+Z。',icon:'undo-2'},
 applyScale:{title:'应用真实比例',description:'使用两个测量点与填写的参考长度，计算并应用场景的统一比例。',icon:'arrow-up-right'},
 clearMeasure:{title:'清除测量点',description:'移除当前两个测量端点并清空本次长度读数，不改变已经应用的比例。',icon:'rotate-ccw'},
 addFloor:{title:'新增楼层',description:'按填写的名称与高度创建新楼层，再为该层绘制可走区域与起始区。',icon:'plus'},
 updateFloor:{title:'更新当前楼层',description:'保存本层名称与楼面标高，并把本层已标注的门、出口和楼梯端点同步到新高度。',icon:'refresh-cw',kind:'action'},
 removeFloor:{title:'删除当前楼层',description:'删除当前选中的楼层及其范围；依赖该层的连接需要重新检查。',icon:'trash-2'},
 previewSmoke:{title:'烟雾扩散预览',description:'按当前楼层、门与楼梯结构计算烟雾影响，检查烟气是否会进入其他区域。',icon:'wind'},
 activateBtn:{title:'激活场景',description:'把已保存的校准版本设为观察主机当前使用的场景；正式采集还需通过发布门槛。',icon:'arrow-up-right'},
 confirmReview:{title:'确认人工复核',description:'完成导航预览后，确认已对照模型检查门洞、墙边、楼梯与出口。',icon:'circle-check'},
 ackAnnotationRecheck:{title:'确认标注已重新核对',description:'记录操作者已在修改比例或楼面高度后，逐层对照模型检查现有可走区、障碍、门、楼梯与出口。确认后仍需重新运行导航预览。',icon:'scan-search'},
 previewNav:{title:'运行导航预览',description:'计算每个可走区能否到达出口，并把不可达区域整理为可定位的检查清单。',icon:'route'},
 saveBtn:{title:'保存校准',description:'把当前楼层、通路与比例写入服务器，并执行服务端结构校验。',icon:'save'},
 dynamicFloor:{title:'切换编辑楼层',description:'切换当前楼层，三维视图与底部楼层剖切会同步到该层。',icon:'layers',kind:'floor'},
 navigationResult:{title:'定位导航结果',description:'选中该可走区并把相机移到问题位置，便于检查边界、门或楼层连接。',icon:'crosshair',kind:'result'},
 observeBtn:{title:'总览视角',description:'回到整栋建筑的观察位置，同时查看楼层之间的人物与通路关系。',icon:'eye',kind:'toggle',group:'observe'},
 floorModeBtn:{title:'楼层观察',description:'聚焦单个楼层并隐藏上方遮挡结构，便于观察本层活动。',icon:'layers',kind:'toggle',group:'observe'},
 replayBtn:{title:'轨迹回放',description:'载入本场历史轨迹，检查人物路径、拥堵位置与疏散时序。',icon:'route',kind:'toggle',group:'observe'},
 autoBtn:{title:'自动旋转',description:'让观察视角缓慢环绕建筑，适合总览演示；进入第一视角会自动停止。',icon:'orbit',kind:'toggle'},
 followBtn:{title:'跟随选中角色',description:'持续把观察视角保持在当前角色附近；可再次点击取消跟随。',icon:'focus',kind:'toggle'},
 firstPersonToggle:{title:'第一视角',description:'切换到选中角色的视点，体验其建筑内视野与行进方向。',icon:'user',kind:'toggle'},
 roofBtn:{title:'显示或隐藏屋顶',description:'切换屋顶遮挡，观察室内平面与楼层结构。',icon:'box',kind:'toggle'},
 clipBtn:{title:'剖切观察',description:'临时剖开建筑外围或上层结构，查看楼内通路与人物位置。',icon:'scan-line',kind:'toggle'},
 stairsBtn:{title:'聚焦楼梯',description:'把相机移到场景中的第一组楼梯，检查上下层连接。',icon:'route'},
 calibrateBtn:{title:'打开场景校准',description:'进入校准工作台，编辑建筑比例、楼层范围、通路与发布状态。',icon:'ruler'},
 refreshBtn:{title:'刷新状态',description:'重新读取连接状态、统计数据和场景运行信息。',icon:'refresh-cw'},
 sceneReload:{title:'重新读取场景',description:'重新请求当前场景的定义与模型资源，用于同步外部校准修改。',icon:'refresh-cw'},
 startBtn:{title:'开始实验',description:'启动本场仿真或真人疏散；正式实验需要已验证比例与在线参与者。',icon:'play'},
 pauseBtn:{title:'暂停或继续',description:'暂停当前仿真时间与人物移动，再次点击从原状态继续。',icon:'pause',kind:'toggle'},
 resetBtn:{title:'重置人群',description:'把当前场次的人物位置和轨迹重置到初始状态。',icon:'rotate-ccw'},
 fireBtn:{title:'随机点火',description:'在可走区域随机生成火源，用于演示烟雾扩散与人员疏散。',icon:'flame'},
 restartBtn:{title:'重新开始一场',description:'清除本场计时、人物、火源、烟雾与门状态，然后立即开始新场次。',icon:'rotate-ccw'},
 fireToolBtn:{title:'点火工具',description:'开启后可在建筑地面点击指定位置，精确放置火源。',icon:'flame',kind:'toggle'},
 npcApply:{title:'应用 NPC 数量',description:'把输入的人数应用到下一次仿真生成或当前场景配置。',icon:'users'},
 speedApply:{title:'应用仿真倍速',description:'用 0.25 至 4 倍速控制仿真时间推进速度。',icon:'play'},
 windApply:{title:'应用风场',description:'按输入的 X/Z 风速改变烟雾漂移方向与速度。',icon:'wind'},
 replayPlayBtn:{title:'播放或暂停回放',description:'控制历史轨迹的播放进度。',icon:'play',kind:'toggle'},
 replayExitBtn:{title:'退出轨迹回放',description:'结束回放并返回实时观察视角。',icon:'x'},
 followSelectedBtn:{title:'跟随选中角色',description:'让观察相机持续跟随面板中选中的角色。',icon:'focus',kind:'toggle'},
 firstPersonBtn:{title:'进入第一视角',description:'从选中角色的视点观察建筑内部。',icon:'user',kind:'toggle'},
 fpRecenterBtn:{title:'恢复行进方向',description:'把第一视角重新对准角色当前的前进方向。',icon:'compass'},
 cancelResetBtn:{title:'取消重置',description:'关闭确认框并保留当前人群状态。',icon:'x'},
 confirmResetBtn:{title:'确认重置人群',description:'把人物位置与轨迹恢复到本场初始状态。',icon:'rotate-ccw'},
 cancelRestartBtn:{title:'取消新场次',description:'关闭确认框，当前实验保持原状态。',icon:'x'},
 confirmRestartBtn:{title:'确认开始新场次',description:'清除本场运行数据并立即开始一场新的实验。',icon:'rotate-ccw'},
 rejoinBtn:{title:'更换参与者',description:'清除本设备的当前参与者身份，重新输入编号并加入实验。',icon:'users'},
 exitButton:{title:'退出实验',description:'停止记录本设备的移动输入；主机上的本场实验仍会继续。',icon:'log-out'},
 participantDoorBtn:{title:'操作附近的门',description:'打开角色附近可通行的门；是否可以开启取决于距离、门状态与场次阶段。',icon:'door-open'},
 padMode:{title:'方向键控制',description:'使用屏幕上的四个方向按钮移动角色，松手即停止。',icon:'keyboard',kind:'radio'},
 stickMode:{title:'摇杆控制',description:'使用虚拟摇杆连续控制移动方向与速度。',icon:'joystick',kind:'radio'},
 consentBtn:{title:'同意并进入',description:'确认参与信息后进入三维场景；需要填写编号并勾选知情同意。',icon:'arrow-right'},
 switchParticipantBtn:{title:'清除并更换参与者',description:'移除本机保存的参与者身份与同意状态，返回编号输入页面。',icon:'users'},
 cancelExitBtn:{title:'取消退出',description:'关闭退出确认窗口并继续本次实验。',icon:'x'},
 confirmExitBtn:{title:'确认退出',description:'停止本设备的输入记录并显示退出完成页面。',icon:'log-out'},
 completeRejoinBtn:{title:'重新加入',description:'返回参与者编号与知情同意页面，加入下一场实验。',icon:'arrow-right'},
 'move:forward':{title:'向前移动',description:'朝角色当前面向的方向前进，松开按钮后停止。',icon:'arrow-up'},
 'move:back':{title:'向后移动',description:'朝角色背面移动，松开按钮后停止。',icon:'arrow-down'},
 'move:left':{title:'向左平移',description:'沿角色左侧横向移动，松开按钮后停止。',icon:'arrow-left'},
 'move:right':{title:'向右平移',description:'沿角色右侧横向移动，松开按钮后停止。',icon:'arrow-right'},
 'door:open':{title:'开门',description:'立即打开这扇门，允许人物通过。',icon:'door-open'},
 'door:close':{title:'关门',description:'关闭这扇门并恢复门体的通行阻挡。',icon:'square'},
 'door:lock':{title:'锁定门',description:'锁定这扇门，阻止角色手动开启。',icon:'shield-check'},
 'door:unlock':{title:'解锁门',description:'解除门的锁定状态，恢复允许开启。',icon:'door-open'},
 'door:fire-block':{title:'启用火阻断',description:'把门设置为防火阻挡状态，限制烟气与火焰通过。',icon:'flame'},
 'door:clear-block':{title:'解除火阻断',description:'恢复门的正常状态，解除防火阻挡。',icon:'wind'},
 dynamicHostFloor:{title:'切换观察楼层',description:'选择全部楼层或单层观察；单层会自动启用剖切以查看室内。',icon:'layers',kind:'host-floor'}
};
const TOOL_TIP_ID='spatial-tooltip';
let tooltip=null,tooltipBody=null,tooltipTitle=null,tooltipDescription=null,tooltipIcon=null,tooltipState=null,tooltipKicker=null;
let activeTipButton=null,hoverTimer=0,focusTimer=0,lastPointerAt=0;
let touchTimer=0,touchOrigin=null,heldButton=null,suppressClickOnce=false;
const originalDescriptions=new WeakMap();
function tooltipKey(button){
 if(button.dataset.tooltipKey)return button.dataset.tooltipKey;
 if(button.dataset.stepTarget)return `step:${button.dataset.stepTarget}`;
 if(button.dataset.move)return `move:${button.dataset.move}`;
 if(button.dataset.doorAction)return `door:${button.dataset.doorAction}`;
 return button.id||'';
}
function configFor(button){return tooltips[tooltipKey(button)]||null}
function buttonLabel(button){
 const text=Array.from(button.childNodes).filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent).join(' ').replace(/\s+/g,' ').trim();
 return button.getAttribute('aria-label')||text||button.dataset.tooltipTitle||'界面操作';
}
function inferIcon(button,config){
 if(button.dataset.tooltipIcon)return button.dataset.tooltipIcon;
 if(config?.icon)return config.icon;
 const svg=button.querySelector('svg.lucide');
 const cls=svg?Array.from(svg.classList).find(name=>name.startsWith('lucide-')):'';
 return cls?cls.slice(7):'circle-dot';
}
function tooltipStateText(button,config){
 if(button.dataset.tooltipState)return button.dataset.tooltipState;
 if(button.disabled||button.getAttribute('aria-disabled')==='true')return button.dataset.tooltipState||'当前不可用 · 悬停说明';
 if(config?.kind==='step'){
  if(button.classList.contains('is-complete'))return '该步骤已完成';
  if(button.classList.contains('active'))return '正在编辑此步骤';
  return '点击切换到该步骤';
 }
 if(config?.kind==='floor')return button.getAttribute('aria-pressed')==='true'?'当前编辑楼层':'点击切换到此层';
 if(config?.kind==='host-floor')return button.classList.contains('active')?'当前观察楼层':'点击切换观察层';
 if(config?.kind==='result')return button.classList.contains('is-unreachable')?'需要处理 · 点击定位':'可达 · 点击定位';
 if(config?.kind==='radio')return button.getAttribute('aria-checked')==='true'?'当前控制方式':'点击切换控制方式';
 if(config?.kind==='tool'){
  if(button.classList.contains('active'))return '当前工具';
  return '点击启用此工具';
 }
 if(config?.kind==='toggle'){
  const active=button.getAttribute('aria-pressed')==='true'||button.classList.contains('active');
   if(config.group&&config.group==='observe'&&!button.classList.contains('active'))return '点击切换到此模式';
  return active?'当前已启用':'点击启用';
 }
 const role=button.getAttribute('role');
 if(role==='tab')return button.getAttribute('aria-selected')==='true'?'当前选中':'点击切换';
 return '可使用';
}
function createTooltip(){
 if(document.getElementById(TOOL_TIP_ID)){tooltip=document.getElementById(TOOL_TIP_ID);return}
 tooltip=document.createElement('div');tooltip.id=TOOL_TIP_ID;tooltip.className='spatial-tooltip';tooltip.setAttribute('role','tooltip');tooltip.setAttribute('aria-hidden','true');
 tooltip.innerHTML='<div class="spatial-tooltip-surface"><div class="spatial-tooltip-top"><span class="spatial-tooltip-kicker"></span><span class="spatial-tooltip-rule" aria-hidden="true"></span></div><div class="spatial-tooltip-content"><span class="spatial-tooltip-icon" aria-hidden="true"></span><div class="spatial-tooltip-copy"><h3></h3><p></p></div></div><div class="spatial-tooltip-footer"><span class="spatial-tooltip-state"><i aria-hidden="true"></i><span></span></span><span class="spatial-tooltip-hint">停留查看</span></div></div><span class="spatial-tooltip-arrow" aria-hidden="true"></span>';
 tooltipBody=tooltip.querySelector('.spatial-tooltip-surface');tooltipKicker=tooltip.querySelector('.spatial-tooltip-kicker');tooltipIcon=tooltip.querySelector('.spatial-tooltip-icon');tooltipTitle=tooltip.querySelector('h3');tooltipDescription=tooltip.querySelector('p');tooltipState=tooltip.querySelector('.spatial-tooltip-state');
 document.body.append(tooltip);
}
function describeButton(button){
 const config=configFor(button);
 const explicitTitle=button.dataset.tooltipTitle;
 let title=explicitTitle||config?.title||buttonLabel(button);
 if(button.dataset.tooltipKey==='dynamicFloor'&&button.getAttribute('aria-pressed')==='true')title='当前编辑楼层 · '+title;
 let description=button.dataset.tooltipDescription;
 if(!description&&config?.description)description=config.description;
 if(!description&&button.dataset.tooltipNativeTitle)description=button.dataset.tooltipNativeTitle;
 if(button.dataset.tooltipKey==='navigationResult'){const reachable=!button.classList.contains('is-unreachable');description=reachable?'该可走区有通路可以到达出口。点击会在三维视图中选中它，便于核对边界与连接。':'该可走区目前无法到达出口。点击会在三维视图中定位问题区域，检查门、楼梯或出口连接。';if(button.dataset.tooltipState==='可达 · 点击定位'||button.dataset.tooltipState==='不可达 · 点击定位')button.dataset.tooltipState=reachable?'可达 · 点击定位':'不可达 · 点击定位';}
 if(!description)description=`执行“${title}”对应的操作。`;
 return {config,title,description,icon:inferIcon(button,config),state:tooltipStateText(button,config),tone:button.dataset.tooltipTone||((button.disabled||button.getAttribute('aria-disabled')==='true')?'off':config?.kind?'on':'ready')};
}
function positionTooltip(button){
 if(!tooltip||!button?.isConnected)return;
 const gap=11,margin=12,rect=button.getBoundingClientRect();
 tooltip.classList.remove('is-visible','is-above','is-below');tooltip.style.left='0px';tooltip.style.top='0px';
 const box=tooltip.getBoundingClientRect(),width=box.width,height=box.height;
 const above=rect.top>=height+gap+margin;
 const belowSpace=innerHeight-rect.bottom;
 const placeAbove=above||belowSpace<height+gap+margin;
 let left=Math.max(margin,Math.min(innerWidth-width-margin,rect.left+rect.width/2-width/2));
 let top=placeAbove?rect.top-height-gap:rect.bottom+gap;
 top=Math.max(margin,Math.min(innerHeight-height-margin,top));
 tooltip.style.left=`${Math.round(left)}px`;tooltip.style.top=`${Math.round(top)}px`;
 tooltip.style.setProperty('--tooltip-arrow-x',`${Math.round(Math.max(20,Math.min(width-20,rect.left+rect.width/2-left)))}px`);
 tooltip.classList.add(placeAbove?'is-above':'is-below');
 requestAnimationFrame(()=>requestAnimationFrame(()=>tooltip.classList.add('is-visible')));
}
function updateTooltip(button,reposition=true){
 const data=describeButton(button);
 tooltipKicker.textContent=document.body.classList.contains('calibration-ui')?'CALIBRATION / FUNCTION':document.body.classList.contains('host-ui')?'OBSERVATORY / CONTROL':'PARTICIPANT / CONTROL';
  tooltipIcon.innerHTML=icon(data.icon);
  tooltipTitle.textContent=data.title;
  tooltipDescription.textContent=data.description;
  const stateText=document.createElement('span');stateText.textContent=data.state;tooltipState.replaceChildren();tooltipState.append(document.createElement('i'),stateText);
 tooltipState.className=`spatial-tooltip-state is-${data.tone}`;
 if(reposition)positionTooltip(button);
}
function showTooltip(button){
 if(!tooltip||!button?.isConnected||!button.matches(':not([data-tooltip="none"])'))return;
 clearTimeout(hoverTimer);clearTimeout(focusTimer);
 if(activeTipButton&&activeTipButton!==button)detachDescribed(activeTipButton);
 activeTipButton=button;
 if(!originalDescriptions.has(button))originalDescriptions.set(button,button.getAttribute('aria-describedby'));
 const before=originalDescriptions.get(button)||'';
 const ids=before.split(/\s+/).filter(Boolean);if(!ids.includes(TOOL_TIP_ID))ids.push(TOOL_TIP_ID);
 button.setAttribute('aria-describedby',ids.join(' '));
 tooltip.setAttribute('aria-hidden','false');updateTooltip(button);
}
function detachDescribed(button){
 if(!button)return;const original=originalDescriptions.get(button);
 if(original===null||original===undefined)button.removeAttribute('aria-describedby');
 else button.setAttribute('aria-describedby',original);
}
function hideTooltip(button=null){
 clearTimeout(hoverTimer);clearTimeout(focusTimer);
 if(button&&activeTipButton!==button)return;
 if(activeTipButton)detachDescribed(activeTipButton);
 activeTipButton=null;if(!tooltip)return;tooltip.classList.remove('is-visible','is-above','is-below');tooltip.setAttribute('aria-hidden','true');
}
function buttonAtPoint(x,y){
 const nodes=document.elementsFromPoint?.(x,y)||[];
 for(const node of nodes){const button=node.closest?.('button');if(button)return button}
 return null;
}
function scheduleHover(button){
// A keyboard-focused button may still own the visible tooltip. Crossing it
// with the pointer must not cancel the focus delay and briefly erase the
// explanation, so hover and focus keep independent timers.
 clearTimeout(hoverTimer);
 if(activeTipButton===button)return;
 hoverTimer=setTimeout(()=>showTooltip(button),420);
}
function bindTooltips(){
 createTooltip();
 document.addEventListener('pointerover',event=>{
  if(event.pointerType==='touch')return;lastPointerAt=performance.now();
  // Native disabled buttons do not reliably emit pointer events in Chromium.
  // Resolve the topmost button at the pointer position instead of relying on
  // event.target, while preserving the disabled state and its action guard.
  const direct=event.target.closest?.('button');
  const button=direct||buttonAtPoint(event.clientX,event.clientY);
  if(!button)return;
  const related=event.relatedTarget?.nodeType===1?event.relatedTarget:null;
  if(related&&button.contains(related))return;
  scheduleHover(button);
 },{passive:true});
 document.addEventListener('pointerout',event=>{
  if(event.pointerType==='touch')return;
  const button=event.target.closest?.('button');if(!button)return;
  const related=event.relatedTarget?.nodeType===1?event.relatedTarget:null;
  if(related&&button.contains(related))return;
  clearTimeout(hoverTimer);if(activeTipButton===button)hideTooltip(button);
 },{passive:true});
document.addEventListener('pointermove',event=>{
  if(event.pointerType==='touch')return;
  const candidate=buttonAtPoint(event.clientX,event.clientY);
  if(activeTipButton){
   if(candidate!==activeTipButton){hideTooltip(activeTipButton);if(candidate)scheduleHover(candidate)}
   return;
  }
  if(candidate)scheduleHover(candidate);
 },{passive:true});
 document.addEventListener('pointerdown',event=>{
  lastPointerAt=performance.now();hideTooltip();clearTimeout(touchTimer);touchOrigin=null;
  if(event.pointerType!=='touch')return;
  const button=event.target.closest?.('button');if(!button)return;
  touchOrigin={x:event.clientX,y:event.clientY};
  touchTimer=setTimeout(()=>{heldButton=button;suppressClickOnce=true;showTooltip(button)},550);
 },{passive:true});
 document.addEventListener('pointermove',event=>{if(event.pointerType==='touch'&&touchOrigin&&Math.hypot(event.clientX-touchOrigin.x,event.clientY-touchOrigin.y)>10){clearTimeout(touchTimer);touchOrigin=null;hideTooltip()}},{passive:true});
 for(const type of ['pointerup','pointercancel'])document.addEventListener(type,event=>{
 clearTimeout(touchTimer);touchOrigin=null;
 // If a platform does not emit the click that normally consumes the hold,
 // expire the one-shot guard instead of blocking a later, intentional tap.
 if(suppressClickOnce)setTimeout(()=>{suppressClickOnce=false;heldButton=null},500);
},{passive:true});
 document.addEventListener('click',event=>{if(suppressClickOnce&&heldButton?.contains(event.target)){event.preventDefault();event.stopImmediatePropagation();suppressClickOnce=false;heldButton=null;}},true);
 document.addEventListener('contextmenu',event=>{if(suppressClickOnce&&heldButton?.contains(event.target))event.preventDefault()});
 document.addEventListener('focusin',event=>{
  const button=event.target.closest?.('button');if(!button)return;
  if(!button.matches(':focus-visible'))return;
  clearTimeout(focusTimer);focusTimer=setTimeout(()=>showTooltip(button),120);
 });
 document.addEventListener('focusout',event=>{const button=event.target.closest?.('button');if(button&&activeTipButton===button)hideTooltip(button)});
 document.addEventListener('keydown',event=>{if(event.key==='Escape')hideTooltip()});
 addEventListener('scroll',()=>hideTooltip(),{capture:true,passive:true});
 addEventListener('resize',()=>hideTooltip(),{passive:true});
 addEventListener('blur',()=>hideTooltip());
}
function prepareTooltip(button){
 if(button.dataset.tooltip==='none')return;
 const nativeTitle=button.getAttribute('title');
 if(nativeTitle){if(!button.dataset.tooltipNativeTitle)button.dataset.tooltipNativeTitle=nativeTitle;button.removeAttribute('title')}
}
function decorate(button){
 prepareTooltip(button);
 const config=buttons[button.id]||null;
 const tipConfig=configFor(button);
 if(!config&&!tipConfig&&!button.dataset.move)return;
 const visibleText=button.textContent.replace(/\s+/g,' ').trim();
 if(!button.getAttribute('aria-label')){
  const accessible=button.dataset.tooltipTitle||tipConfig?.title||config?.[1]||visibleText||button.id;
  if(accessible)button.setAttribute('aria-label',accessible);
 }
 if(tipConfig){
  if(!button.dataset.tooltipTitle)button.dataset.tooltipTitle=tipConfig.title;
  if(!button.dataset.tooltipDescription)button.dataset.tooltipDescription=tipConfig.description;
 }
 if(button.closest('[data-skip-icons]'))return;
 const moveIcons={forward:'arrow-up',back:'arrow-down',left:'arrow-left',right:'arrow-right'};
 const expected=moveIcons[button.dataset.move]||config?.[0]||tipConfig?.icon||'circle-dot';
 const existingSvg=button.querySelector('svg.lucide');
 const existingIcon=existingSvg?Array.from(existingSvg.classList).find(name=>name.startsWith('lucide-'))?.slice(7):'';
 if(existingSvg&&existingIcon===expected)return;
 // Dynamic floors and step links own structured markup. Place the icon in
 // their existing marker so their number, label and grid remain intact.
 if(button.dataset.tooltipKey==='dynamicFloor'){
  const marker=button.querySelector('.floor-name');
  if(marker&&!marker.querySelector('svg.lucide'))marker.insertAdjacentHTML('afterbegin',icon(expected));
  return;
 }
 if(button.dataset.stepTarget){
  const marker=button.querySelector('.step-label');
  if(marker&&!marker.querySelector('svg.lucide'))marker.insertAdjacentHTML('afterbegin',icon(expected));
  return;
 }
 // Buttons declared only in the tooltip table can contain custom child nodes
 // (navigation results and other generated controls). Add an icon without
 // replacing those nodes or their click target.
 if(!config){
  button.insertAdjacentHTML('afterbegin',icon(expected));
  return;
 }
 const text=button.dataset.move?'':(config[1]||visibleText);
 button.replaceChildren();button.insertAdjacentHTML('beforeend',icon(expected));
 if(text){const label=document.createElement('span');label.textContent=text;button.append(label);}
}

export function initSpatialUI(kind){
 document.body.classList.add('silver-ui',kind==='host'?'host-ui':kind==='participant'?'participant-ui':'calibration-ui');
 mountIcons();bindTooltips();document.querySelectorAll('button').forEach(decorate);
 document.querySelectorAll('.calibration-ui details summary').forEach(summary=>summary.insertAdjacentHTML('beforeend',icon('chevron-down')));
 const observer=new MutationObserver(records=>{
  const candidates=new Set();
  for(const record of records){const el=record.target.nodeType===1?record.target:record.target.parentElement;const button=el?.closest?.('button');if(button)candidates.add(button);for(const added of record.addedNodes)if(added.nodeType===1){if(added.matches('button'))candidates.add(added);added.querySelectorAll?.('button').forEach(b=>candidates.add(b));}}
  candidates.forEach(decorate);
  if(activeTipButton&&candidates.has(activeTipButton)){if(activeTipButton.isConnected)updateTooltip(activeTipButton);else hideTooltip(activeTipButton)}
 });
 observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['title','aria-pressed','aria-checked','disabled','class']});
 if(kind==='host'){
  const stage=document.querySelector('.stage'),panel=document.querySelector('.panel');panel.id='hostControlSheet';
  stage.insertAdjacentHTML('beforeend','<div class="spatial-hero"><div class="spatial-kicker">SPATIAL / OBSERVATORY</div><h1>疏散观察</h1><div class="hero-rule"></div><p>从每一次移动，看见空间中的选择。<br>实时观察人物、通路与楼层之间的关系。</p></div>');
  const title=document.querySelector('.hud .title');title.innerHTML=icon('building-2')+'<span>学校场景 / 观察台</span>';
  document.body.insertAdjacentHTML('beforeend','<nav class="spatial-links host-links" style="position:fixed;right:27px;top:24px;z-index:10" aria-label="工作台导航"><a href="/" aria-current="page">观察控制</a><a href="/calibrate?scene=school">场景校准</a></nav>');
  const toggle=document.createElement('button');toggle.className='spatial-panel-toggle';toggle.innerHTML=icon('sliders-horizontal')+'<span>参数</span>';toggle.dataset.tooltipTitle='主机参数面板';toggle.dataset.tooltipDescription='展开或收起场次、场景、门与观察设置。';toggle.dataset.tooltipIcon='sliders-horizontal';toggle.setAttribute('aria-controls',panel.id);toggle.setAttribute('aria-expanded','true');toggle.onclick=()=>{const closed=panel.classList.toggle('is-collapsed');toggle.setAttribute('aria-expanded',String(!closed));};document.body.append(toggle);decorate(toggle);
  if(innerWidth<760){panel.classList.add('is-collapsed');toggle.setAttribute('aria-expanded','false');}
 }
 if(kind==='participant'){
  const title=document.querySelector('.brand');const subtitle=title.querySelector('small');title.replaceChildren();title.insertAdjacentHTML('afterbegin','<span>空间实验 / 参与端</span>');if(subtitle)title.append(subtitle);
  const card=document.querySelector('#consentCard');card.insertAdjacentHTML('afterbegin','<div class="consent-kicker">'+icon('shield-check')+'<span>SPATIAL / PARTICIPANT</span></div>');
 }
}
