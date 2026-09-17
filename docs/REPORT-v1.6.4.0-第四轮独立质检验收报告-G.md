# v1.6.4.0 AI Copilot 第四轮独立质检验收报告（G）

> **质检报告编号**：QC4-REPORT-v1.6.4.0-20260917-G-REV1  
> **质检日期**：2026-09-17  
> **被测代码基线**：Git Commit `d8ccb3d4fdf809ffceefbb12c8b8ceaa1e8a4a58`  
> **前序质检基线**：`621c05d42fc9e6f540fc251e4b305c3dca01c576`（QC3 阻断基线）  
> **设计规格基线**：Rev.D＋N-10 架构规范（冻结提交 `4fb1f2e`）  
> **验收主体**：智能体 G（独立质检验收责任人）  
> **终审依据**：用户核心三大关切要求、生产部署准入标准、真实浏览器人类真机实测、物理网络线路抓包  

---

## 1. 质检终审裁决与总体结论

### 1.1 总体裁决结果

$$\Large\mathbf{第四轮质检验收结论：【不通过】（QC4\ REJECT\ /\ NO-GO）}$$
$$\text{当前基线存在重大前端页面脱位错位缺陷（QC4-UI01），严禁作为 v1.6.4.0 生产发布！}$$

经过智能体 G 对修复提交 `d8ccb3d` 进行的全盘深度代码走查、受控独立隔离守护进程拉起、真实物理链路抓包复验、全量单元测试执行，并经**人类真机浏览器真实视觉走查**，终审判定：

1. **【后端与接口闭环】上一轮致命缺陷（QC3-B01）彻底整改闭环**：多轮会话出站历史摘要提取已修复，单测对齐（125/125 Passed），真实网关抓包证实第二轮、第三轮提问已 100% 携带前序轮次问答摘要，后端多轮上下文记忆与候选 SQL 校验完全正常。
2. **【前端重大阻断缺陷（新发现）】Copilot 专家助手页与 AI 配置页严重排版错位、脱离主内容区被挤压至最底部（QC4-UI01，BLOCKER）**：
   - 在真实浏览器中，人类用户点击左侧菜单“Copilot 专家助手”或“AI 配置”时，**右侧主工作区呈现大面积空旷全黑盲区，实际页面卡片被错误排版至视口 100vh 最底部**！用户必须用力向下滚动页面才能看到内容。
   - **根本原因**：`frontend/index.html` 中，开发人员 Q 将 `copilot-page`（第 2920 行）和 `copilot-admin`（第 2981 行）错误地写在了 `<div class="content-area">` 和 `<div class="page-content">` 的闭合标签（第 2575-2576 行）**之外**，直接脱离了主布局容器，违反了基本的前端页面布局规范。
3. **质检自我检讨与盲区反思**：
   - 此前无头浏览器（Headless Playwright）自动化脚本仅校验了 DOM 节点是否存在、按钮是否可点击、横向有无滚动条，自动化截图虽记录了下半部分元素，但未进行视口 Y 轴几何坐标与顶部大面积空旷异常的视觉断言，导致这一明显的视觉错位缺陷险些漏网。
   - 智能体 G 坚决以人类用户的实际交互视觉体验为最终准绳，推翻初次粗略判定，严把质量门禁，**正式驳回本次发布申请**。

---

## 2. 用户核心三大关切问题复核结论

| 序号 | 用户核心关切问题 | 质检裁决 | 现场实测核心事实与判定说明 |
|:---:|---|:---:|---|
| **1** | **在 AI 配置界面人类是不是能够在 WebUI 界面顺利进行大模型 API 的配置？** | **【功能可用，但界面严重错位】<br>UI 阻断 (BLOCKER)** | **业务逻辑闭环，但由于布局脱位，人类视觉与交互体验极差（QC4-UI01）：**<br>① 功能上，Provider 抽屉、场景路由、双管理员授权均已实现且逻辑可用；<br>② 但**在浏览器中进入“AI 配置”时，页面不是显示在右侧正常工作区，而是掉落到了页面最底部，上面留有整整一屏的大黑屏**，严重不符合人类使用习惯与工业级软件交付标准，必须修复。 |
| **2** | **是不是能够通过 API 接口正确的调用大模型？** | **【完全能够】<br>PASS** | **大模型接口调用健壮，且具备受控沙箱安全防御能力：**<br>① 标准 Bearer Token 与 TLS 连通正常，主备切换可用；<br>② 当大模型返回带修复建议的候选 SQL（如 `SELECT * FROM qc_candidate_missing;`）时，后端受控 `_t09_validate` 校验器平稳介入，输出：`validation: INCOMPLETE, executable: UNKNOWN, semantic_equivalence: NOT_PROVEN`，无 `AttributeError` 崩溃，轮次终态 100% 正常达到 `SUCCEEDED`。 |
| **3** | **能不能和 Copilot 助手你一言我一句的交互式对话咨询本项目的相关内容？** | **【功能可用，但主页面脱落】<br>UI 阻断 (BLOCKER)** | **多轮会话记忆完全连贯，网络线路抓包确证上下文真实生效（QC3-B01 终结）；但独立助手页严重排版错位（QC4-UI01）：**<br>① **右侧滑出抽屉（Drawer）**：对话交互、多轮记忆、物理抓包第 2 轮带 1 条历史、第 3 轮带 2 条历史均已完美证实；<br>② **左侧菜单进入“Copilot 专家助手”独立大页面时**：**整个对话卡片被挤到了页面最底部**，上方是大面积漆黑盲区，严重影响人类用户咨询体验。 |

---

## 3. 本轮新增阻断缺陷深度剖析与施工级修复方案

### 缺陷 QC4-UI01（BLOCKER）：`copilot-page` 与 `copilot-admin` 脱离主内容容器导致页面整体掉底与巨幅黑屏

#### 1. 现场故障现象与用户截图佐证
- **现场实况**：在标准 1080P 分辨率浏览器中访问 `http://localhost:8000/`：
  - 点击左侧菜单“Copilot 专家助手” $\rightarrow$ 右侧上半部分呈现大面积无内容黑屏，对话框出现在滚动条最底部；
  - 点击左侧菜单“系统管理 -> AI 配置” $\rightarrow$ 右侧同样出现大面积黑屏盲区，模型提供商表格与 Tab 页被排在最底部。
- **视觉缺陷截图**：用户人工走查截屏及证据归档 [docs/evidence/v1.6.4.0-qc4-g/shots/qc4-14-copilot-dedicated-page.png](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.4.0-qc4-g/shots/qc4-14-copilot-dedicated-page.png) 与 [qc4-15-ai-config-models.png](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.4.0-qc4-g/shots/qc4-15-ai-config-models.png)。

#### 2. 代码级根因定位
定位文件：[frontend/index.html](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html)

**正确的整体布局框架**为：
```html
Line 134:   <div class="content-area">
Line 135:     <div class="breadcrumb-bar">...</div>
Line 140:     <div class="page-content">
Line 142:       <div v-if="currentPage==='dashboard'">...</div>
...
Line 2563:      <div v-if="currentPage==='sys-perms'">...</div>
Line 2574:
Line 2575:    </div> <!-- ❌ 此处闭合了 page-content -->
Line 2576:  </div>   <!-- ❌ 此处闭合了 content-area -->
Line 2577: </div>     <!-- ❌ 闭合主容器 -->
Line 2578: </div>     <!-- ❌ 闭合 #app -->
```
随后是各类弹窗 `<el-dialog>`（行 2580 ~ 2917）。

而在行 2919，开发人员 Q 新增的代码：
```html
Line 2919:  <!-- ═══ v1.6.4.0 / CP-1：Copilot 专家助手页 ═══ -->
Line 2920:  <div v-if="currentPage==='copilot-page'" class="page-content" data-testid="copilot-page">
...
Line 2978:  </div>
Line 2979:
Line 2980:  <!-- ═══ v1.6.4.0 / CP-1：AI配置（管理区） ═══ -->
Line 2981:  <div v-if="currentPage==='copilot-admin'" class="page-content" data-testid="copilot-admin-page">
...
Line 3214:  </div>
...
Line 3350: </div> <!-- ❌ 孤立闭合标签 -->
```

**错误机理**：
1. `copilot-page` 和 `copilot-admin` 被写在了主容器 `.content-area` 的外面！
2. CSS 规定 `.content-area { flex: 1; overflow-y: auto; display: flex; flex-direction: column }`，在没有子页面匹配时，它依然占据整屏（100vh）；
3. 放在容器外部的 `copilot-page` 和 `copilot-admin` 只能排在整个主页面容器的下方，直接坠落到了窗口最底部，导致视觉上上方留有一整屏的巨大黑洞！

---

### 4. 针对开发人员 Q 的施工级修复指南

请开发人员 Q 严格按照以下 4 个步骤进行修复（严禁在外部补丁堆砌 CSS）：

#### 第一步：迁移两个页面 DOM 到主容器内部
打开 [frontend/index.html](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html)：
- 剪切 **第 2919 行至第 2978 行**（`<!-- ═══ v1.6.4.0 / CP-1：Copilot 专家助手页 ═══ -->` 整个块）；
- 剪切 **第 2980 行至第 3214 行**（`<!-- ═══ v1.6.4.0 / CP-1：AI配置（管理区） ═══ -->` 整个块）；
- 移动粘贴至 **第 2574 行**（即在 `currentPage==='sys-perms'` 模块的闭合 `</div>` 之后，且在第 2575 行 `</div> <!-- 闭合 page-content -->` 之前）。

#### 第二步：规范最外层 class 属性
在迁移进 `<div class="page-content">` 之后：
- 将原第 2920 行：
  ```html
  <div v-if="currentPage==='copilot-page'" class="page-content" data-testid="copilot-page">
  ```
  修改为（去除重复的 `class="page-content"`，与其他页面保持一致）：
  ```html
  <div v-if="currentPage==='copilot-page'" data-testid="copilot-page">
  ```
- 将原第 2981 行：
  ```html
  <div v-if="currentPage==='copilot-admin'" class="page-content" data-testid="copilot-admin-page">
  ```
  修改为：
  ```html
  <div v-if="currentPage==='copilot-admin'" data-testid="copilot-admin-page">
  ```

#### 第三步：清理末尾孤立标签
- 删除第 3350 行孤立的 `</div>`，保持全局 HTML 标签配对严格平衡。

#### 第四步：本地真机视觉验证
- 启动 `uvicorn backend.main:app --port 8000`；
- 在真实 Chrome/Edge 浏览器中点击左侧“Copilot 专家助手”和“AI 配置”，肉眼确认内容立即展示在右侧主区域面包屑正下方，上方无大面积空黑，无需滚动即可正常交互。

---

## 5. 前序轮次整改闭环情况核销台账

| 缺陷编号 | 所属轮次 | 缺陷描述 | 严重级别 | 整改提交 | 状态 | 说明 |
|---|---|---|---|---|:---:|---|
| **QC2-B01** | QC2 | 历史解密缺少 AAD 导致抛出异常 | BLOCKER | `621c05d` | **CLOSED** | 已通过 |
| **QC2-B02** | QC2 | 候选 SQL 校验未绑定 `_t09_validate` 导致崩溃 | BLOCKER | `621c05d` | **CLOSED** | 已通过 |
| **QC3-B01** | QC3 | 历史摘要字段层级解析错误致出站 `history: []` 恒空 | BLOCKER | `d8ccb3d` | **CLOSED** | 经网络抓包验证彻底解决 |
| **QC4-UI01** | **QC4** | **`copilot-page`/`copilot-admin` 脱离主内容容器坠底大黑屏** | **BLOCKER** | **待整改** | **OPEN** | **本轮新立项，阻断发布** |

---

## 6. 终审裁决与签署

| 审查维度 | 准入标准 | 本轮实测结果 | 判定 |
|---|---|---|:---:|
| **后端逻辑与大模型通信** | 协议健全，多轮会话记忆出站携带历史摘要 | 抓包确证携带完整历史，T09 校验稳健 | **合格** |
| **原有 11 大核心功能** | 零退化、零接口报错、零次生灾害 | 11 大模块全量点击回归正常 | **合格** |
| **WebUI 页面布局规范** | 页面各组件位置合理，符合人类视觉与使用习惯 | **Copilot 主页与 AI 配置严重错位掉底 (QC4-UI01)** | **不合格 (BLOCKER)** |

### 验收签署意见

> **智能体 G 终审签字**：  
> 本人作为 v1.6.4.0 版本第四轮独立质检验收责任人，在人类用户亲自上机排查出的重大界面脱位缺陷面前，坚决杜绝“重接口轻体验”、“重自动化轻肉眼走查”的官僚主义倾向。  
> 确认 `QC4-UI01` 严重损害用户直观体验，不符合企业级产品交付规范。  
> 
> **正式裁决：第四轮质检不通过（QC4 REJECT / NO-GO），驳回发布申请，责令开发人员 Q 限期整改！**

---
*报告归档路径：`docs/REPORT-v1.6.4.0-第四轮独立质检验收报告-G.md`*  
*现场缺陷截屏归档：`docs/evidence/v1.6.4.0-qc4-g/shots/`*
