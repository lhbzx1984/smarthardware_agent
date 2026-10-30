<h1 align = "center">嘉立创EDA专业版文件格式</h1>

<p align = "center">v3.x</p>












































<p align = "center" >嘉立创EDA在线编辑器：<a href=https://pro.lceda.cn/editor>https://pro.lceda.cn/editor</a></p>
<p align = "center" >嘉立创EDA客户端下载：<a href=https://lceda.cn/page/download>https://lceda.cn/page/download</a></p>



**文档说明**：

-	本文档会跟随编辑器的新功能更新而持续更新。
-	最新版本请前往 [嘉立创EDA使用教程.pdf](https://lceda.cn/page/download) 下载


# cn / format


# 嘉立创EDA 文件格式


---

## 嘉立创EDA文件格式

### V2.2版本的文件格式

V2.2版本的文件格式：[lceda-pro-file-format-v2.2_2022.12.15.zip](https://image.lceda.cn/files/lceda-pro-file-format-v2.2_2022.12.15.zip)

提示：这不是最新的文件格式规范，但这里有绝大部分的格式细节。

### V3版本的文件格式

V3版本使用全新的源码格式，完全不同于之前的格式。

提示：嘉立创EDA 3.0 全面改造了工程格式，使用日志的概念对工程变更进行了增量存储；并且从 2.0 的按照数组偏移量的设计风格，调整成了按照 key-value 的风格，便于后期格式演进，和相关兼容代码的开发，以及提高可读性。

V3版本的文件格式（zip里面包含范例文件）：
- [lceda-pro-file-format-v3_2025.10.21.zip](https://image.lceda.cn/files/lceda-pro-file-format-v3_2025.10.21.zip)
- [lceda-pro-file-format-v3_2025.10.21.md](https://image.lceda.cn/files/lceda-pro-file-format-v3_2025.10.21.md)
- [lceda-pro-file-format-v3_2025.10.21.pdf](https://image.lceda.cn/files/lceda-pro-file-format-v3_2025.10.21.pdf)

#### 格式详情文档

- [工程格式](/cn/format/project/index.md)
- [原理图格式](/cn/format/schematic/index.md)
- [PCB格式](/cn/format/pcb/index.md)

#### 基础文档格式

##### 文档约定

-   工程的所有数据存在一个日志文档里，日志记录变动的首行必须是文件头 `DOCTYPE`，以区分不同类型文档， `DOCTYPE`作为一个 `块级元素`存在于工程日志中
-   以行为单位，每一行都是由两个合法的 JSON 对象拼接而成，第一个对象是用于最终一致性框架解析使用，后一个是 `原子结构对象`
-   键名采用驼峰命名，每个单词的首字母大写（除了第一个单词的首字母外），并且单词之间没有下划线或其他分隔符
-   所有图元都要带上文件内的 `唯一编号`
-   `旋转角度` 以逆时针方向为正，统一使用角度制
-   若无特殊说明，所有坐标、长度、大小统一使用 `0.01 inch` 为单位
-   所有颜色都使用 `"#RRGGBB"` 的方式表达，如果需要表示无颜色（完全透明），则用 `""`
-   所有使用 `是否XXXX` 描述的属性，都使用 `1` 是 `0` 否 的编码方式
-   本 `约定` 中未明确描述的部分（如转义等），全部依据 [RFC 7195 The JavaScript Object Notation (JSON) Data Interchange Format](https://tools.ietf.org/html/rfc7159)

##### 文档头

-   所有数据的开头必须带上文档头，以区分不同类型文档
-   逐行解析时，当解析遇到文档头时，表示接下来解析的数据都归到该文档下，直到遇到下一个文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "SCH_PAGE", "uuid": "UUID", "client": "clientID" }|
```

1. type："DOCHEAD"，文档头标识
2. docType：文档类型
3. uuid：文档唯一编号，工程内唯一
4. client：最终一致性的一个终端标识

###### docType 文档类型

-   工程配置 `PROJECT_CONFIG`
-   板子 `BOARD`
-   原理图 `SCH`
-   原理图页 `SCH_PAGE`
-   PCB `PCB`
-   面板 `PANEL`
-   符号 `SYMBOL`
-   封装 `FOOTPRINT`
-   器件 `DEVICE`
-   真彩图 `BLOB`
-   实例属性 `INSTANCE`

##### 数据格式

```json
{ "type": "TYPE", "id": "UUID", "ticket": 1}||{["key": string]: any}|
```

通过标识符`||`, 将一条数据分割成内外两层数据：

1. 外层数据：用于最终一致性框架使用，保证数据的一致性
    1. type 数据类型
    2. id 唯一编号，需保证在文档内具体的 type 下唯一
    3. ticket 逻辑时钟，仅用于最终一致性框架，保证数据的唯一
2. 内层数据: 图元原子数据，是一个`key-value`对象，具体的数据内容详见各类型对应的文档

之所以分为内外两层数据，是因为在最终一致性框架中，无需关注具体的图元数据，只需要外层数据就能保证数据的唯一，以减少不必要的 JSON 解析，节省内存消耗

```json
{ "type": "META", "ticket": 1}||{"name": "名称"}|
```

有一些数据是在文档内就只能保留一个数据，用 type 字段就能表示唯一，会省略掉 id 字段，只有同一类型下有多个数据时才用 id 字段去区分

##### 最终一致性

-   存储 3.0，数据增删改都是往日志里加一条数据，这样日志里存在多条数据是表示的是同一个数据
-   最终一致性框架根据 type、id、ticket 字段决定保留哪一条数据，保证数据的一致性

ps: 更加详细的描述请查看最终一致性的设计文档，这里仅说明相关字段的用处

###### type 相同且 id 相同的两条数据

```json
{ "type": "TYPE", "id": "UUID", "ticket": 1}||{"data": 1}|
```

```json
{ "type": "TYPE", "id": "UUID", "ticket": 2}||{"data": 2}|
```

最终一致性框架，就会去对比 ticket 字段，ticket 更大的则保留下来，ticket 小的则舍弃，上面 data 为 2 的数据则保留

###### type 相同且 id 相同，ticket 也相同

```json
{ "type": "DOCHEAD" }||{ "docType": "SCH_PAGE", "uuid": "UUID", "client": "1" }|
{ "type": "TYPE", "id": "UUID", "ticket": 1}||{"data": 1}|
```

```json
{ "type": "DOCHEAD" }||{ "docType": "SCH_PAGE", "uuid": "UUID", "client": "2" }|
{ "type": "TYPE", "id": "UUID", "ticket": 1}||{"data": 2}|
```

则去比较文档头的 client 字段，client 标识更小的则保留下来，上面 data 为 1 的数据则保留

##### 删除

###### 原子数据删除

```json
{ "type": "TYPE", "id": "UUID", "ticket": 1}||""
```

数据的删除实际就是将内层数据置为空字符串

###### 文档删除

```json
{ "type": "DELETE_DOC", "ticket": 1}||{"isDelete": true}|
```

1. type `DELETE_DOC` 文档删除标识
2. isDelete 是否删除

-   文档删除实际是添加一个删除的标识，并不会在日志内直接删除文档数据，以方便文档删除的撤销和数据的一致性维护

-   所有删除的数据都会在日志里有所保留，用户如果要去除工程内删除的数据记录，让用户走克隆工程以去除这些数据，日志的快照不会去清除相关删除的记录


## 工程格式


---

### 工程日志格式

该文档仅用来描述属于工程但不属于画布的数据，具体的画布数据请查看单独的描述文档。

#### 目录

- [公共数据](/cn/format/project/common.md) - 包含基本信息、创建信息、修改信息等
- [基本信息](/cn/format/project/meta.md) - 包含工程配置、板子、原理图、PCB、面板、符号、封装、器件等元数据
- [BLOB 真彩图](/cn/format/project/blob.md) - 真彩图数据格式
- [实例值属性覆盖](/cn/format/project/instance.md) - 实例值属性覆盖格式
- [变体](/cn/format/project/variant.md) - 变体数据格式
- [元件分组](/cn/format/project/group.md) - 元件分组数据格式


---

### 公共数据

每个文档都会带上基本信息、创建信息、修改信息，这些信息由工程管理维护

##### 基本信息

```json
{ "type": "META", "ticket": 1 }||data
```

1. type "META" 基本信息
2. data 具体数据，每个文档不一样，后续单独描述

##### 创建信息

每个文档一样

```json
{ "type": "META_CREATE", "ticket": 1 }||
{
    "creator": { "uuid":"UUID", "nickname":"nickname", "username":"username", },
    "createTime": 1725593026474,
}|
```

1. type "META_CREATE" 创建信息
2. creator 创建者
3. createTime 创建时间

##### 修改信息

```json
{ "type": "META_MODIFY", "ticket": 1 }||
{
    "modifier": { "uuid":"UUID", "nickname":"nickname", "username":"username", },
    "createTime": 1725593026474,
}|
```

1. type "META_MODIFY" 修改信息
2. modifier 修改者
3. updateTime 更新时间


---

### 基本信息

##### 工程配置

存放工程的配置数据，之前是存在后端的工程详情里的

```json
{ "type": "META", "ticket": 1 }||{ "name": "name", "default_sheet": "default_sheet_uuid"}|
```

1. name 工程名称
2. default_sheet 工程的默认图纸

##### 板子

```json
{ "type": "META", "ticket": 1 }||{ "title": "title", "sort": 1}|
```

1. title 板子名称
2. sort 排序权重

##### 原理图

```json
{ "type": "META", "ticket": 1 }||{ "name": "name", "board":"", "source": "", "version": ""}|
```

1. name 名称
2. board 所属的板子 uuid, 没有则为空
3. source 源文档 uuid
4. version 版本，源文档的更新时间

##### 原理图页

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Page Name",
    "schematic": "SID",
    "description": "描述",
    "sort": 0.5,
    "source": "",
    "version": "",
}|
```

1. name 名称
2. schematic 所属的原理图 uuid
3. description 描述
4. sort 排序权重
5. source 源文档 uuid
6. version 版本，源文档的更新时间

##### PCB

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "PCB Name",
    "board": "boardId",
    "description": "描述",
    "parentId":"",
    "sort": 0.5,
    "source": "",
    "version": "",
}|
```

1. name 名称
2. board 所属的板子 uuid, 没有则为空
3. description 描述
4. parentId 父 PCB UUID, 没有则为空
5. sort 排序权重
6. source 源文档 uuid
7. version 版本，源文档的更新时间

子 PCB

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "子PCB Name",
    "board": "",
    "description": "描述",
    "parentId":"UUID",
    "sort": 0.2,
    "source": "",
    "version": "",
}|
```

##### 面板

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Panel Name",
    "description": "描述",
    "sort": 0.5,
}|
```

1. name 名称
2. description 描述
3. sort 排序权重

##### 符号

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Name",
    "description": "描述",
    "type": 2,
    "tags": "tag1",
    "source": "",
    "version": "",
}|
```

1. name 名称
2. description 描述
3. type 符号类型
4. tags 分类标签
5. source 源文档 uuid
6. version 版本，源文档的更新时间

##### 封装

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Name",
    "description": "描述",
    "type": 4,
    "tags": "tag1",
    "source": "",
    "version": "",
    "pcb": "",
}|
```

1. name 名称
2. description 描述
3. type 封装类型
4. tags 分类标签
5. source 源文档 uuid
6. version 版本，源文档的更新时间
7. pcb 所属的 pcb uuid, 默认为空，仅特殊封装有该字段

特殊封装

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Name",
    "description": "描述",
    "type": 4,
    "tags": "tag1",
    "source": "",
    "version": "",
    "pcb": "pcbUuid",
}|
```

##### 器件

```json
{ "type": "META", "ticket": 1 }||
{
    "name": "Name",
    "description": "描述",
    "type": 4,
    "tags": "tag1",
    "source": "",
    "version": "",
    "attributes": {},
    "images": "",
}|
```

1. name 名称
2. description 描述
3. tags 分类标签
4. source 源文档 uuid
5. version 版本，源文档的更新时间
6. attributes 器件属性 Key-Value
7. images 图片链接


---

### BLOB 真彩图

```json
{ "type": "META", "id": "BLOB-HASH-ID",  "ticket": 1 }||{ "filename": "dadas", "data": "data:image/png;base64,asdfasdfwer" }|
```

1. filename 文件名
2. data 预留元数据，二进制数据：使用类似 `Data URLs` 的规范，但有区别

    1. 一般格式，与 `Data Urls` 完全兼容 `data:[<mediatype>][;base64],<data>`

        - 如 `data:image/png;base64,asdfasdfwer`
        - 如 `data:text/html,<html></html>`
        - 一般情况下，应尽可能使用一般格式，方便直接使用 Web API 加载，不需要算法转换

    2. 扩展格式，扩展了其功能性，加上了如 gzip/deflate 等编码转换功能 `data:<mediatype>[pipeline],<data>`

    - 如 `data:text/html;gzip;base64,asdfasdf`
        1. 先将 asdfasdf 进行 base64 解码
        2. 再用 gzip 解压缩
        3. 最后才以 text/html 去加载
    - 如 `data:text/css;deflate;aes128;base64,aaaaaaa`
        1. 先将 aaaaaaa 进行 base64 解码
        2. 再用 aes128 解密（具体加解密逻辑待定）
        3. 再用 deflate 解压缩
        4. 最后才以 text/css 去加载此数据

```json
{ "type": "META", "id": "ID",  "ticket": 1 }||{ "filename": "dadas", "data": "data:application/vnd.ms-excel;base64,xxsasdfawerwerqwer" }|
```


---

### 实例值属性覆盖

#### 文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "INSTANCE", "uuid": "SCH-UNIQUE-ID_$5|e100_$1|e55_$6|e15_$8", "client": "clientID" }|
```

1. type：`DOCHEAD`，文档头标识
2. docType：`INSTANCE` 实例值属性覆盖
3. uuid：层次编号，工程内唯一，已`_`分割 id
    1. 第一个是顶层原理图编号
    2. 最后一个只到 Sheet 编号
    3. 中间所有的都是使用编号组合语法定位的 `Block Symbol`，如 `$1|e2`，其中 `$1` 为 Sheet 编号，`e2` 为 `Block Symbol` 编号
4. client：最终一致性的一个终端标识

#### 属性覆盖

```json
{ "type": "INSTANCE_ATTR", "id": "e176",  "ticket": 1 }||data
```

1. type `INSTANCE_ATTR` 实例属性覆盖
2. id 图元编号
3. data 属性覆盖，数据签名为 `{ [parentId: string]: { [key: string]: string }| }|`

```json
{ "type": "DOCHEAD" }||{ "docType": "INSTANCE", "uuid": "SCH-UNIQUE-ID_$5|e100_$1|e55_$6|e15_$8", "client": "clientID" }|
{ "type": "INSTANCE_ATTR", "id": "e176",  "ticket": 1 }||{ "Designator": "U15", "ASDF": "1234" }|
{ "type": "INSTANCE_ATTR", "id": "e176e5",  "ticket": 1 }||{ "NUMBER": 2 }|
{ "type": "INSTANCE_ATTR", "id": "e178",  "ticket": 1 }||{ "Author": "abc" }|
```


---

### 变体

#### 文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "VARIANT", "uuid": "UUID", "client": "clientID" }|
```

1. type：`DOCHEAD`，文档头标识
2. docType：`VARIANT` 变体数据
3. uuid: 唯一标识，随机 id
4. client：最终一致性的一个终端标识

#### META 基本信息

```json
{ "type": "META", "ticket": 1 }||
{
	/** 名称 */
	"title": string,
	/** 描述 */
	"description": string,
	/** 原理图 */
	"schematicId": string,
	/** 排序 */
	"zIndex": number,
	/** 是否有未归组 */
	"notGrouped": boolean,
}|
```


---

### 元件分组

#### 文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "COMPONENT_GROUP", "uuid": "UUID", "client": "clientID" }|
```

1. type：`DOCHEAD`，文档头标识
2. docType：`COMPONENT_GROUP` 元件分组
3. uuid: 唯一标识，随机 id
4. client：最终一致性的一个终端标识

##### META 基本信息

```json
{ "type": "META", "ticket": 1 }||
{
	/** 名称 */
	"title": string,
	/** 子 元件分组的 父 元件分组 uuid */
	"parent": string,
	/** 属于直接变体的元件分组 不参与 元件分组树构建 */
	"belong": string,
	/** 元件分组树中排序 */
	"zIndex": number,
	/** 原理图 */
	"schematicId": string,
}|
```

##### GROUP_INDEX 元件分组在变体的排序

```json
{ "type": "GROUP_INDEX", "ticket": 1, "id": "variantId" }||
{
	"zIndex": 1,
}|
```

1. type：`GROUP_INDEX`
2. ticket: 逻辑时钟
3. id：变体 uuid
4. zIndex：在变体中的排序

##### GROUP_DATA 属性数据

```json
{ "type": "GROUP_DATA", "id": "e176@uuid",  "ticket": 1 }||data
```

1. type `GROUP_DATA` 属性数据
2. id 组合 id,以`@`分割：
    1. 第一个是图元 id
    2. 第二个是实例页 id
3. data 属性覆盖，数据签名为 `{ [key: string]: string }| `

```json
{ "type": "GROUP_DATA", "id": "e176@uuid1",  "ticket": 1 }||{ "Designator": "U15", "ASDF": "1234" }|
{ "type": "GROUP_DATA", "id": "e177@uuid1",  "ticket": 2 }||{ "NUMBER": 2 }|
{ "type": "GROUP_DATA", "id": "e176@uuid2",  "ticket": 3 }||{ "Author": "abc" }|
```


## 原理图格式


---

### SCH 原理图类型文档

本章节详细介绍了嘉立创EDA原理图文件的格式规范。原理图文件主要包含以下几个部分：

- [通用配置](/cn/format/schematic/common.md)：文档头和画布配置信息。
- [结构元素](/cn/format/schematic/structure.md)：PART 子库和 GROUP 组合。
- [属性](/cn/format/schematic/attr.md)：图元属性定义。
- [导线](/cn/format/schematic/wire.md)：导线、总线和总线入口。
- [文本](/cn/format/schematic/text.md)：文本元素。
- [形状](/cn/format/schematic/shape.md)：矩形、多边形、圆形、圆弧、贝塞尔曲线、椭圆。
- [引脚](/cn/format/schematic/pin.md)：引脚定义。
- [元件](/cn/format/schematic/component.md)：原理图元件实例。
- [对象](/cn/format/schematic/obj.md)：二进制对象（图片等）。
- [表格](/cn/format/schematic/table.md)：表格和单元格。


---

### 通用格式

#### 文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "SCH_PAGE", "uuid": "UUID", "client": "clientID" }|
```

```json
{ "type": "DOCHEAD" }||{ "docType": "SYMBOL", "uuid": "UUID", "client": "clientID" }|
```

-   type："DOCHEAD"，文档头标识
-   docType：文档类型，"SCH_PAGE"：原理图、 "SYMBOL"：符号
-   uuid：文档唯一编号，工程内唯一
-   client：最终一致性的一个终端标识

#### 画布配置

编辑器附加信息，用于数据分析等功能，目前已占用的一些字段

```json
{ "type": "CANVAS", "ticket": 1 }||
{
  "originX":0,
  "originY":0,
}|
```

1. type："CANVAS"，画布配置信息标识
2. ticket 逻辑时钟
3. originX 画布原点 X
4. originY 画布原点 Y


---

### 结构图元

#### PART 子库

`PART` 只有符号页才有
如果是单 Part 器件也必须有一个 `PART`，子库编号留空 `""`

```json
{ "type": "PART","id":"partId", "ticket": 1 }||{"BBOX": [-10, -20, 10, 20]}|
```

1. type 子库图元：PART
2. id 子库编号
3. ticket 逻辑时钟
4. 内部参数：Key-Value
    - 预留 `BBOX` 为能刚好框住 PART 下所有图元的矩形包围盒任意对角的两个点
      **XTools 核心逻辑不应关注这个属性**
    - 其它为编辑器附加信息，用于数据分析等功能，可选

符号页的所有图元带上对应的子库编号，表示归属于该子库

```json
{ "type": "WIRE", "id": "UUID", "ticket": 1 }||
{
    "partId": "partId", // 符号图元都带上该子库id
    "groupId": 0,
    "locked": false,
    "zIndex": 0.235,
    "dots": [
        [310, 550, 400, 550, 400, 460],
        [480, 460, 400, 460],
        [400, 330, 400, 460]
    ],
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

#### GROUP 分组控制

```json
{ "type": "GROUP", "id": "UUID", "ticket": 1 }||{ "groupId": "1", "parentId": "0", "title": "Logo" }|
```

1. type 分组控制 `GROUP`
2. id 唯一编号
3. ticket 逻辑时钟
4. groupId 分组编号，不能为 0
5. parentId 父级分组编号，为 0 则表示无父级
6. title 分组名称，无名称为空字符串 `""`


---

### ATTR 属性

ATTR 是一个较为通用的图元，其含义为

1. 表达一个由 键(KEY)——值(VALUE) 组成的的多个属性中的一个
1. 可以在画布上显示，并控制显示哪些内容，以及样式位置等

当隶属编号没有指定时，则默认隶属于当前块级图元上

```json
{ "type": "ATTR", "id": "UUID", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": false, "zIndex": 0.1, "parentId": "UUID", "key":"string", "value":"string", "keyVisible":false, "valueVisible":false, "positionX":200, "positionY":200, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

1. type 属性名称：ATTR
2. id 唯一编号
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. parentId 隶属编号：隶属于哪个图元，`""` 表示属于当前块 _默认块是文件_
9. key 属性 Key
10. value 属性 Value
11. keyVisible 是否显示 Key
12. valueVisible 是否显示 Value
13. positionX 位置 X：未显示过的属性位置固定为 `null`
14. positionY 位置 Y：未显示过的属性位置固定为 `null`
15. rotation 旋转角度，绕 `位置` 旋转
16. color 颜色
17. fillColor 背景色
18. fontFamily 字体名称
19. fontSize 字体大小，与坐标等单位相同
20. strikeout 是否加删除线
21. underline 是否加下划线
22. italic 是否斜体
23. fontWeight 是否加粗
24. vAlign 垂直对齐模式：0 顶部对齐 1 中间对齐 2 底部对齐
25. hAlign 水平对齐模式：0 左对齐 1 居中 2 右对齐

当属性 Value 中含有 `~` 字符时，XTools 需要实现从开始到结束，遇到第奇数个 `~` 开始文字带上划线，遇到第偶数个 `~` 结束文字带上划线


---

### 连线图元

#### WIRE 导线

```json
{ "type": "WIRE", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 0.235,
    "dots": [
        [310, 550, 400, 550, 400, 460],
        [480, 460, 400, 460],
        [400, 330, 400, 460]
    ],
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：WIRE
2. id 唯一编号
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. dots 坐标：分成多段线，每段都是连续的一组 X1 Y1 X2 Y2 X3 Y3 ... 描述的线
9. strokeColor 颜色，null 为默认
10. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
11. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
12. strokeWidth 宽度，null 为默认
13. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

```json
{ "type": "ATTR", "id": "e200", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e271", "key":"NET", "value":"GND", "keyVisible":true, "valueVisible":true, "positionX":108, "positionY":804.5, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

导线必须带上 NET 属性标识网络名称

#### BUS 总线

```json
{ "type": "BUS", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 0.235,
    "dots": [
        [310, 550, 400, 550, 400, 460],
        [480, 460, 400, 460],
        [400, 330, 400, 460]
    ],
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：BUS
2. id 唯一编号
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. dots 坐标：分成多段线，每段都是连续的一组 X1 Y1 X2 Y2 X3 Y3 ... 描述的线
9. strokeColor 颜色，null 为默认
10. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
11. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
12. strokeWidth 宽度，null 为默认
13. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

```json
{ "type": "ATTR", "id": "e200", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e271", "key":"NET", "value":"A[1:5]", "keyVisible":true, "valueVisible":true, "positionX":108, "positionY":804.5, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

总线必须带上 NET 属性标识网络名称

#### BUSENTRY 总线接入标识

![image](../public/storage/images/cn/format/schematic/wire/ripper.png)

-   如图所示
    -   浅黄色为 `BUS`
    -   绿色圆角菱形，以及其向右延伸的一个类似 `PIN` 的图形，为 `BUSENTRY`
    -   蓝色为 `Wire`
-   端点为 `WIRE` 和 `BUSENTRY` 类似 `PIN` 的最右侧端点接触的那个端点的坐标
-   因为 `WIRE` 和 `BUS` 可以是任意角度接入的，所以需要指定其旋转方向以和 `WIRE` 的接入方向一致，例如图内是 180 度
-   `BUSENTRY` 固定一格长
-   `BUSENTRY` 具体的图形，由 XTools 最终解释，不在格式内限定

```json
{ "type": "BUSENTRY", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 0.235,
    "busGroupId": 4,
    "order": 4,
    "pointX": 500,
    "pointY": 600,
    "rotation": 90,
}|
```

1. type 图元名称：BUSENTRY
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. busGroupId 顺序编号：在隶属的 BUS 里的顺序编号，可重复
   比如 BUS 网络为 A[2:3]B[7:6] 可以具有 0 1 2 3 0 1 2 3 ... 一系列顺序编号的 BUSENTRY，其中
   0 一定代表分支 A2B7
   1 一定代表分支 A2B6
   2 一定代表分支 A3B7
   3 一定代表分支 A3B6
9. pointX 端点 X
10. pointY 端点 Y
11. rotation 旋转角度：绕 `端点` 旋转


---

### TEXT 文本

```json
{ "type": "TEXT", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 4.12,
    "positionX": 109,
    "positionY": 804.5,
    "rotation": 0,
    "value": "任意字符doukeyi@!@#$",
    "color": "#fff",
    "fillColor": "#fff",
    "fontFamily": "宋体",
    "fontSize": 12,
    "strikeout": false,
    "underline": false,
    "italic": false,
    "fontWeight": false,
    "vAlign": 0 ,
    "hAlign": 2,
}|
```

1. type 图元名称：TEXT
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. positionX 文本坐标 X
9. positionY 文本坐标 Y
10. rotation 旋转角度，绕 文本坐标 旋转
11. value 文本内容：任意字符
12. color 颜色
13. fillColor 背景色
14. fontFamily 字体名称
15. fontSize 字体大小，与坐标等单位相同
16. strikeout 是否加删除线
17. underline 是否加下划线
18. italic 是否斜体
19. fontWeight 是否加粗
20. vAlign 垂直对齐模式：0 顶部对齐 1 中间对齐 2 底部对齐
21. hAlign 水平对齐模式：0 左对齐 1 居中 2 右对齐


---

### 形状图元

#### RECT 矩形

矩形由其对角的两个点定义，其旋转是绕`点1`进行的

```json
{ "type": "RECT", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 7.35,
    "dotX1": 340,
    "dotY1": 210,
    "dotX2": 100,
    "dotY2": 200,
    "radiusX": 40,
    "radiusY": 30,
    "rotation": 90,
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：RECT
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. dotX1 点 1 X
9. dotY1 点 1 Y
10. dotX2 点 2 X
11. dotY2 点 2 Y
12. radiusX 圆角半径 X：`0` 表示非圆角
13. radiusY 圆角半径 Y：`0` 表示非圆角
14. rotation 旋转角度：绕 `点1` 旋转
15. strokeColor 颜色，null 为默认
16. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
17. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
18. strokeWidth 宽度，null 为默认
19. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

#### POLY 多边形

```json
{ "type": "POLY", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 7.35,
    "points": [390, 260, 450, 300, 560, 280, 540, 320],
    "closed": false,
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：POLY
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. points 点集坐标：X Y X Y X Y ...
9. closed 是否自动闭合：如果自动闭合，则结束点会自动连上起始点
10. strokeColor 颜色，null 为默认
11. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
12. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
13. strokeWidth 宽度，null 为默认
14. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

#### CIRCLE 圆

```json
{ "type": "CIRCLE", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 2.332,
    "centerX": 430,
    "centerY": 200,
    "radius": 21,
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：CIRCLE
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. centerX 圆心 X
9. centerY 圆心 Y
10. radius 半径 r
11. strokeColor 颜色，null 为默认
12. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
13. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
14. strokeWidth 宽度，null 为默认
15. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

#### ARC 圆弧

```json
{ "type": "ARC", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 0.8689,
    "startX": -10,
    "startY": 0,
    "referX": 0,
    "referY": 10,
    "endX": 10,
    "endY": 0,
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：ARC
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. startX 起始 X
9. startY 起始 Y
10. referX 参考 X
11. referY 参考 Y
12. endX 结束 X
13. endY 结束 Y
14. strokeColor 颜色，null 为默认
15. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
16. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
17. strokeWidth 宽度，null 为默认
18. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

#### BEZIER 三阶贝塞尔线条

```json
{ "type": "BEZIER", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 2.1002,
    "controls": [10, 10, 30, 20, 100, 30, 50, 70],
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：BEZIER
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. controls 控制点：X1 Y1 X2 Y2 X3 Y3 X4 Y4 ...
9. strokeColor 颜色，null 为默认
10. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
11. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
12. strokeWidth 宽度，null 为默认
13. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线

#### ELLIPSE 椭圆

```json
{ "type": "ELLIPSE", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 6.23,
    "centerX": 670,
    "centerY": 325,
    "radiusX": 220,
    "radiusY": 20,
    "rotation": 0,
    "strokeColor": null,
    "strokeStyle": 0,
    "fillColor": "",
    "strokeWidth": null,
    "fillStyle": 1,
}|
```

1. type 图元名称：ELLIPSE
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. centerX 中点 cx
9. centerY 中点 cy
10. radiusX 水平半径 rx
11. radiusY 垂直半径 ry
12. rotation 旋转角度 rot
13. strokeColor 颜色，null 为默认
14. strokeStyle 样式：0 实线 1 短划线 2 点线 3 点划线
15. fillColor 填充颜色："" 不填充，填充自动闭合起始点和结束点
16. strokeWidth 宽度，null 为默认
17. fillStyle 填充样式：0 无 1 实心 2 网格 3 横线 4 竖线 5 菱形 6 左斜线 7 右斜线


---

### PIN 标号

![image](../public/storage/images/cn/format/schematic/pin/pin.png)

如图所示

-   所有 PIN 的 `位置 X/Y` 都是离黑色矩形最远的那个端点
-   PIN 1 为 0 度旋转方向，引脚样式 0
-   PIN 2 为 90 度旋转方向，引脚样式 1
-   PIN 3 为 180 度旋转方向，引脚样式 2
-   PIN 4 为 270 度旋转方向，引脚样式 3

```json
{ "type": "PIN", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 2.8772,
    "display": true,
    "electric": 0,
    "positionX": 350,
    "positionY": 170,
    "length": 20,
    "rotation": 0,
    "color": "#880000",
    "pinShape": 3,
}|
```

1. type 图元名称：PIN
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. display 是否显示
9. electric 电气特性：0 UNKNOWN 1 INPUT 2 OUTPUT 3 BI
10. positionX 位置 X
11. positionY 位置 Y
12. length 引脚长度
13. rotation 旋转角度：0 90 180 270
14. color 引脚颜色
15. pinShape 引脚样式：1 Clock 2 DOT，可支持按位或运算，比如 3 = 1 | 2
    举例说明：`0` 无附加 `1` Clock `2` DOT `3` Clock & DOT

```json
{ "type": "ATTR", "id": "e184", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e102", "key":"NAME", "value":"VCC", "keyVisible":true, "valueVisible":true, "positionX":108, "positionY":804.5, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

PIN 必须具有 NAME 属性

```json
{ "type": "ATTR", "id": "e185", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e102", "key":"NUMBER", "value":"1", "keyVisible":true, "valueVisible":true, "positionX":108, "positionY":804.5, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

PIN 必须具有 NUMBER 属性


---

### COMPONENT

-   `COMPONENT` 引用了 Symbol，Symbol 支持多 PART，所以带了 `子库编号` 属性指示具体哪一个，如果是单 PART 则使用默认值 `""`
-   `COMPONENT` 下可绑定许多 `ATTR`，具体的属性行为将由工具定义

##### Symbol 类型

| Symbol 类型编号 | Symbol 类型     | 说明                                                                                                                                                                                                                   |
| :-------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2               | Part Symbol     | 普通器件                                                                                                                                                                                                               |
| 17              | Block Symbol    | 层次图符号                                                                                                                                                                                                             |
| 18              | NetFlag Symbol  | 全局网络符号                                                                                                                                                                                                           |
| 19              | NetPort Symbol  | 层次图网络导出符号                                                                                                                                                                                                     |
| 20              | Sheet Symbol    | 专用于提供原理图图纸的重用机制                                                                                                                                                                                         |
| 21              | NoneElec Symbol | 无电气特性符号 NoneElec 是一类不具有 `PIN` 的无电气特性图元<br>也可以用作特殊的图标，版权声明文字等的重用机制<br>NoneElec 全称 None Electrical，不具有电气特性的意思                                                   |
| 22              | Short Symbol    | 短接符 Short Symbol 是一个特殊 Symbol，必须具有两个 `PIN`<br>所有与同一个 `Short Symbol` 的 `PIN` 相连的网络，将在电气特性上对其进行短接<br>比如网络 A 连接到了 PIN1，网络 B 连接到了 PIN2，则表示 A 和 B 是同一个网络 |

##### Component 图元

```json
{ "type": "COMPONENT", "id": "UUID", "ticket": 1 }||
{
    "partId": "e176",
    "groupId": 0,
    "positionX": 300,
    "positionY": 200,
    "rotation": 15,
    "isMirror": false,
    "data": {},
}|
```

1. type COMPONENT 标识：COMPONENT
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号
5. groupId 分组编号，不能为 0，没有默认为空
6. zIndex Z 轴高度：所有非属性子元素高度分布在 Z0 ~ Z9 范围内，比如 Z 为 23.554，子元素自动分布在 23.5540 ~ 23.5549 范围内
7. positionX 位置 X
8. positionY 位置 Y
9. rotation 旋转角度：绕 `位置` 旋转
10. isMirror 是否镜像
11. data 纯数据属性：附加信息，用于编辑器内部的一些逻辑

Component 所引用的 Symbol 图元一定是按照如下顺序执行的变换

1. 按照 `旋转角度` 绕原点（0,0）逆时针旋转
2. 如果 `是否镜像` 为 `1`，则绕原点（0,0）所在的 Y 轴进行水平镜像
3. 根据 `位置` 进行平移

或者可以理解成如下等价的变换（但是实现更繁琐一些）

1. 根据 `位置` 进行平移
2. 按照 `旋转角度` 绕 `位置` 逆时针旋转
3. 如果 `是否镜像` 为 `1`，则绕 `位置` 所在的 Y 轴进行水平镜像

```json
{ "type": "ATTR", "id": "e187", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e176", "key":"Device", "value":"device-uuid-1", "keyVisible":true, "valueVisible":true, "positionX":300, "positionY":200, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

Device uuid，与 project.json 里 devices 对应的文件名称一致

```json
{ "type": "ATTR", "id": "e188", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e176", "key":"Symbol", "value":"symbol-uuid-1", "keyVisible":true, "valueVisible":true, "positionX":300, "positionY":200, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

`COMPONENT` 内的 `ATTR` 会对模板内同名属性覆盖，覆盖 Symbol 后会影响此器件对符号的绑定

```json
{ "type": "ATTR", "id": "e188", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e176", "key":"Footprint", "value":"footprint-uuid-1", "keyVisible":true, "valueVisible":true, "positionX":300, "positionY":200, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

`COMPONENT` 内的 `ATTR` 会对模板内同名属性覆盖，覆盖 Footprint 后会影响此器件对封装的绑定

```json
{ "type": "ATTR", "id": "e188", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e176", "key":"Designator", "value":"U1", "keyVisible":true, "valueVisible":true, "positionX":300, "positionY":200, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

`COMPONENT` 内的 `ATTR` 会对模板内同名属性覆盖

```json
{ "type": "ATTR", "id": "e180", "ticket": 1 }||
{ "partId": "", "groupId": 0, "locked": true, "zIndex": 0.1, "parentId": "e176e5", "key":"NUMBER", "value":"1", "keyVisible":true, "valueVisible":true, "positionX":108, "positionY":804.5, "rotation":0, "color":null, "fillColor":null, "fontFamily":null, "fontSize":null, "strikeout":null, "underline":null, "italic":null, "fontWeight":null, "vAlign":0, "hAlign":2,}|
```

`PIN` 属性覆盖的方式关键在 `ATTR` 的 `隶属编号` 上
编号分两部分，例如 `e176e5`，其中 `e176` 为 `COMPONENT` 的编号，`e5` 为在模板内的 `PIN` 编号


---

### OBJ 二进制内嵌对象

内嵌于图页上的图片和文件等数据，可作为附件下载，以及直接显示（EDA 自行决定，不在格式内要求）

```json
{ "type": "OBJ", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "locked": false,
    "zIndex": 9.0876,
    "fileName": "a.txt",
    "startX": 200,
    "startY": 300,
    "width": 10,
    "height": 20,
    "rotation": 0,
	"isMirror": false,
	"content": "data:text/plain;base64,MTIzNA==",
}|
```

1. type 二进制内嵌对象标识：OBJ
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. locked 是否锁定
7. zIndex Z 轴高度
8. fileName 文件名
9. startX 左上角 X
10. startY 左上角 Y
11. width 宽
12. height 高
13. rotation 旋转角度：绕`左上角`旋转
14. isMirror 是否镜像
15. content 二进制数据，有两种模式
    1. 一般格式，遵循 `Data Urls` 规范 `data:[<mediatype>][;base64],<data>`
        - 如 `data:image/png;base64,asdfasdfwer`
        - 如 `data:text/html,<html></html>`
    2. BLOB 引用模式，`blob:hashid`


---

### 表格图元

#### TABLE 表格

```json
{ "type": "TABLE", "id": "UUID", "ticket": 1 }||
{
    "partId": "",
    "groupId": 0,
    "startX": 30,
    "startY": 40,
    "rowSizes": [32, 40, 32, 32, 25],
    "colSizes": [33, 33, 44, 44, 11],
    "rowLocked": [0, 0, 1, 0, 0],
	"colLocked": [0, 1, 0, 0, 0],
    "rotation": 0,
}|
```

1. type 表格：`TABLE`
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. partId 子库编号，符号页专属，原理图忽略该字段
5. groupId 分组编号，不能为 0，没有默认为空
6. startX 左上角 X
7. startY 左上角 Y
8. rowSizes 行高
9. colSizes 列宽
10. rowLocked 行锁定
11. colLocked 列锁定
12. rotation 旋转角度

#### TABEL_CELL 表格单元格

```json
{ "type": "TABLE", "id": "UUID", "ticket": 1 }||
{
    "tableId": "tableId",
    "value": "abc\ndef\nghi",
    "rowIndex": 2,
    "columnIndex": 5,
    "rowSpan": 1,
    "colSpan": 3,
	"topStyle": {
        "strokeColor": null,
	    "strokeStyle": 0,
	    "fillColor": "",
	    "strokeWidth": null,
	    "fillStyle": 1,
    },
    "rightStyle": {
        "strokeColor": null,
	    "strokeStyle": 0,
	    "fillColor": "",
	    "strokeWidth": null,
	    "fillStyle": 1,
    },
    "bottomStyle": {
        "strokeColor": null,
	    "strokeStyle": 0,
	    "fillColor": "",
	    "strokeWidth": null,
	    "fillStyle": 1,
    },
    "leftStyle": {
        "strokeColor": null,
	    "strokeStyle": 0,
	    "fillColor": "",
	    "strokeWidth": null,
	    "fillStyle": 1,
    },
    "leftStyle": {
        "strokeColor": null,
	    "strokeStyle": 0,
	    "fillColor": "",
	    "strokeWidth": null,
	    "fillStyle": 1,
    },
    "fontStyle": {
        "color": "#fff",
	    "fillColor": "#fff",
	    "fontFamily": "宋体",
	    "fontSize": 12,
	    "strikeout": false,
	    "underline": false,
	    "italic": false,
	    "fontWeight": false,
	    "vAlign": 0 ,
	    "hAlign": 2,
    },
    "lineHeight": 12,
}|
```

1. type 表格单元格：`TABLE_CELL`
2. id 编号：文件内唯一
3. ticket 逻辑时钟
4. tableId 表格编号
5. value 内容
6. rowIndex 行
7. columnIndex 列
8. rowSpan 宽度（占多少列）
9. colSpan 高度（占多少行）
10. topStyle 边框线形样式（上）
11. rightStyle 边框线形样式（右）
12. bottomStyle 边框线形样式（下）
13. leftStyle 边框线形样式（左）
14. fontStyle 字体样式
15. lineHeight 行间距


## PCB 格式


---

### PCB 格式文档

本章节详细介绍了嘉立创EDA PCB文件的格式规范。PCB文件主要包含以下几个部分：

- [通用格式](/cn/format/pcb/common.md)：文档头、画布、层、物理层、偏好等。
- [分区格式](/cn/format/pcb/partition.md)：分区定义。
- [基础图元](/cn/format/pcb/primitive.md)：网络、图元配置、分组、丝印配置、关联、属性等。
- [焊盘与过孔](/cn/format/pcb/pad_via.md)：焊盘和过孔。
- [形状图元](/cn/format/pcb/shape.md)：直线、圆弧、多边形体系、折线、填充、区域、覆铜、图片、泪滴等。
- [二进制对象](/cn/format/pcb/obj.md)：内嵌对象。
- [3D 外壳](/cn/format/pcb/3d.md)：外壳、折痕、实体、螺丝柱。
- [文字体系](/cn/format/pcb/text.md)：文字。
- [属性](/cn/format/pcb/attr.md)：属性。
- [尺寸工具](/cn/format/pcb/dimension.md)：尺寸标注。
- [封装体系](/cn/format/pcb/component.md)：元件实例。
- [设计规则](/cn/format/pcb/rule.md)：规则模板、规则定义、规则选择器。
- [拼版](/cn/format/pcb/panel.md)：拼版参数。


---

### 通用格式

#### 文档头

```json
{ "type": "DOCHEAD" }||{ "docType": "PCB", "uuid": "UUID", "client": "clientID" }|
```

```json
{ "type": "DOCHEAD" }||{ "docType": "FOOTPRINT", "uuid": "UUID", "client": "clientID" }|
```

-   type："DOCHEAD"，文档头标识
-   docType：文档类型，"PCB" PCB, "FOOTPRINT" 封装
-   uuid：文档唯一编号，工程内唯一
-   client：最终一致性的一个终端标识

#### CANVAS 画布配置

```json
{ "type": "CANVAS", "ticket": 1 }||
{
  "originX":0,
  "originY":0,
  "unit":"mm",
  "gridXSize":10,
  "gridYSize":10,
  "snapXSize":1,
  "snapYSize":1,
  "altSnapXSize":0.1,
  "altSnapYSize":0.1,
  "gridType":1,
  "multiGridType":0,
  "multiGridRatio":5,
}|
```

1. type 画布配置 `CANVAS`
2. ticket 逻辑时钟
3. originX 画布原点 X
4. originY 画布原点 Y
5. unit **显示**单位（不会影响格式里数据的单位）
6. gridXSize 网格尺寸 X
7. gridYSize 网格尺寸 Y
8. snapXSize 栅格尺寸 X
9. snapYSize 栅格尺寸 Y
10. altSnapXSize Alt 栅格尺寸 X
11. altSnapYSize Alt 栅格尺寸 Y
12. gridType 网格类型：`0` 无 `1` 网格 `2` 网点
13. multiGridType 加粗网格类型：`0` 无 `1` 网格 `2` 网点
14. multiGridRatio 加粗网格倍数：number

#### LAYER 层配置

-   所有 `SIGNAL` `PLANE` `SUBSTRATE` 出现的顺序隐含了它的物理堆叠顺序
-   所有 `SIGNAL` `PLANE` `SUBSTRATE` 数量不受限制
-   格式不假设层编号与层维持稳定的关系，具体实现由工具决定

```json
{ "type": "LAYER","id": 0, "ticket": 1 }||
{
  "layerType":"TOP",
  "layerName":"Top Layer",
  "status":1,
  "activeColor":"#FF0000",
  "activateTransparency":0.5,
  "inactiveColor":"#880000",
  "inactiveTransparency":0.3,
}|
```

1. type 层 `LAYER`
2. id 层编号：唯一
3. ticket 逻辑时钟
4. layerType 层类型
5. layerName 层别名，需要唯一
6. status 状态：`1` 使用 `2` 显示 `4` 锁定，可通过相加叠加状态，例如
    - 使用并显示 3 = 1 + 2
    - 使用并锁定但不显示 5 = 1 + 4
    - 使用并显示并锁定 7 = 1 + 2 + 4
7. activeColor 激活颜色
8. activateTransparency 激活透明度
9. inactiveColor 非激活颜色
10. inactiveTransparency 非激活透明度

```json
["LAYER",2,"BOTTOM","Bottom Layer",1,"#0000ff",1,"#00007f",1]
["LAYER",3,"TOP_SILK","Top Silkscreen Layer",1,"#ffcc00",1,"#7f6600",1]
["LAYER",4,"BOT_SILK","Bottom Silkscreen Layer",1,"#66cc33",1,"#336619",1]
["LAYER",5,"TOP_SOLDER_MASK","Top Solder Mask Layer",1,"#800080",1,"#400040",1]
["LAYER",6,"BOT_SOLDER_MASK","Bottom Solder Mask Layer",1,"#aa00ff",1,"#55007f",1]
["LAYER",7,"TOP_PASTE_MASK","Top Paste Mask Layer",1,"#808080",1,"#404040",1]
["LAYER",8,"BOT_PASTE_MASK","Bottom Paste Mask Layer",1,"#800000",1,"#400000",1]
["LAYER",9,"TOP_ASSEMBLY","Top Assembly Layer",1,"#33cc99",1,"#19664c",1]
["LAYER",10,"BOT_ASSEMBLY","Bottom Assembly Layer",1,"#5555ff",1,"#2a2a7f",1]
["LAYER",11,"OUTLINE","Board Outline Layer",1,"#ff00ff",1,"#7f007f",1]
["LAYER",12,"MULTI","Multi-Layer",1,"#c0c0c0",1,"#606060",1]
["LAYER",13,"DOCUMENT","Document Layer",1,"#ffffff",1,"#7f7f7f",1]
["LAYER",14,"MECHANICAL","Mechanical Layer",1,"#f022f0",1,"#781178",1]
["LAYER",50,"SUBSTRATE","Dialectric1",3,"#999966",1,"#4c4c33",1]
["LAYER",15,"SIGNAL","Inner1",3,"#999966",1,"#4c4c33",1]
["LAYER",52,"SUBSTRATE","Dialectric3",3,"#999966",1,"#4c4c33",1]
["LAYER",51,"SUBSTRATE","Dialectric2",3,"#999966",1,"#4c4c33",1]
["LAYER",17,"SIGNAL","Inner2",3,"#008000",1,"#004000",1]
["LAYER",58,"SUBSTRATE","Dialectric9",3,"#999966",1,"#4c4c33",1]
["LAYER",16,"SIGNAL","Inner3",3,"#00ff00",1,"#007f00",1]
["LAYER",53,"SUBSTRATE","Dialectric4",3,"#999966",1,"#4c4c33",1]
["LAYER",47,"HOLE","Hole Layer",1,"#222222",1,"#111111",1]
["LAYER",48,"SHELL","3D Shell Layer",1,"#222222",1,"#111111",1]
["LAYER",49,"TOP_SHELL","Top 3D Shell Layer",1,"#222222",1,"#111111",1]
["LAYER",50,"BOT_SHELL","Bottom 3D Shell Layer",1,"#222222",1,"#111111",1]
```

#### LAYER_PHYS 层物理特性配置

```json
{ "type": "LAYER_PHYS","id": 0, "ticket": 1 }||
{
  "material":"COPPER",
  "thickness":1,
  "permittivity":0,
  "lossTangent":0,
  "isKeepIsland":0,
}|
```

1. type 层物理特性 `LAYER_PHYS`
2. id 层编号
3. ticket 逻辑时钟
4. material 层材质
5. thickness 厚度
6. permittivity 介电常数
7. lossTangent 损耗切线
8. isKeepIsland 内电层是否保留孤岛

```json
{ "type": "LAYER_PHYS","id": 2, "ticket": 1 }||
{
  "material":"COPPER",
  "thickness":1,
  "permittivity":0,
  "lossTangent":0,
  "isKeepIsland":0,
}|
```

```json
{ "type": "LAYER_PHYS","id": 15, "ticket": 1 }||
{
  "material":"COPPER",
  "thickness":1,
  "permittivity":0,
  "lossTangent":0,
  "isKeepIsland":1,
}|
```

```json
{ "type": "LAYER_PHYS","id": 50, "ticket": 1 }||
{
  "material":"COPPER",
  "thickness":10,
  "permittivity":4.5,
  "lossTangent":0.02,
  "isKeepIsland":0,
}|
```

#### ACTIVE_LAYER 配置当前激活层

```json
{ "type": "ACTIVE_LAYER", "ticket": 1 }||
{
  "layerId":0,
}|
```

1. type 当前激活层 `ACTIVE_LAYER`
2. ticket 逻辑时钟
3. layerId 层序号

#### SILK_OPTS 丝印配置

丝印配置目前主要用于彩色丝印工艺

```json
{ "type": "SILK_OPTS","id": 3, "ticket": 1 }||
{
  "defaultColor":"#ffffff",
  "baseColor":"#000000",
}|
```

1. type 丝印配置：SILK_OPTS
2. id 层编号，层：只有 顶层丝印 与 底层丝印
3. ticket 逻辑时钟
4. defaultColor 默认颜色
5. baseColor 底色

```json
{ "type": "SILK_OPTS","id": 4, "ticket": 1 }||
{
  "defaultColor":"#020202",
  "baseColor":"#aaaaaa",
}|
```

#### PREFERENCE 偏好

```json
{ "type": "PREFERENCE", "ticket": 1 }||
{
  "startTrackWidthFollowLast":0,
  "lastTrackWidth":10,
  "startViaSizeFollowLast":0,
  "lastViaInnerDiameter":39.37,
  "lastViaDiameter":78.74,
  "snap":1,
  "routingMode":2,
  "routingCorner":"L45",
  "removeLoop":1,
  "rotatingObject":0,
  "trackFollow":0,
  "stretchTrackMinCorner":1,
  "preferenceConfig":null,
  "realTimeUpdateUnusedLayers":0,
  "unusedPadRange":3,
  "pushVia":"OPTIMIZA_OPEN",
  "pathOptimization4BePushed":0,
  "currentPathOptimization4BePushed":"OPTIMIZA_WEAK",
  "removeCircuitsContainingVias":1,
  "removeAntenna":1,
}|
```

1. type 偏好 `PREFERENCE`
2. ticket 逻辑时钟
3. startTrackWidthFollowLast 起始布线是否跟随上次设置
4. lastTrackWidth 上次布线宽度
5. startViaSizeFollowLast 起始打孔尺寸是否跟随上次设置
6. lastViaInnerDiameter 上次打孔内径
7. lastViaDiameter 上次打孔外径
8. snap 是否自动吸附
9. routingMode 布线模式：`0` 无 `1` 推挤 `2` 环绕 `3` 阻挡
10. routingCorner 布线拐角模式
    - `"L45"` 线条 45 度
    - `"L90"` 线条 90 度
    - `"R45"` 圆弧 45 度
    - `"R90"` 圆弧 90 度
    - `"L"` 线条自由角度
    - `"R"` 圆弧自由角度
11. removeLoop 布线是否自动移除回路
12. rotatingObject 是否单对象旋转
13. trackFollow 导线是否跟随封装移动
14. stretchTrackMinCorner 拉伸导线最小拐角比率（比线宽）
15. preferenceConfig 层堆叠偏好来源
16. realTimeUpdateUnusedLayers 是否自动移除未使用焊盘
17. unusedPadRange 移除未使用焊盘的范围
    1. 全部
    2. 仅焊盘
    3. 仅过孔
18. pushVia 推挤过孔
19. pathOptimization4BePushed 路径优化（单段/整段）
20. currentPathOptimization4BePushed 当前导线路径优化
21. removeCircuitsContainingVias 是否移除有过孔的回路
22. removeAntenna 是否移除天线

#### ITEM_ORDER 图元顺序建议

提供给 PCB 图元顺序建议，PCB 可以依照这个信息安排图元顺序，这个信息只能出现一次

```json
{ "type": "ITEM_ORDER",  "ticket": 1 }||{ "ids":["e2", "e1"] }|
```

1. type 图元顺序建议：ITEM_ORDER
2. ticket 逻辑时钟
3. ids 图元编号：有两种编码形式
    1. 普通编号：形式为 `/^[a-z]+\d+$/i`，如 `e1` `e123` 等
    2. 封装实例编号：形式为 `/^[a-z]+\d+[a-z]+\d+$/i`，用于如封装中的丝印等，如 `e1e5` `e12e22` 等

这里说明下为什么叫做 “建议”，因为比如 `e1` 是顶层图元，`e2` 是底层图元，那么

```json
{ "type": "ITEM_ORDER",  "ticket": 1 }||{ "ids":["e2", "e1"] }|
```

默认依然是 `e1` 在上面，除非有什么特殊操作导致底层置顶了


---

### 分区格式

PCB 引入了分区设计，分区设计需要标记大量元素的分区信息，所以采用了如下格式设计

#### PARTITION 分区图元

```json
{ "type": "LAYER_PHYS","id": "UUID", "ticket": 1 }||
{
  "name":"分区1",
  "fileUuid":"SUB-PCB-UUID",
  "path":"复杂多边形",
}|
```

1. type 分区：`PARTITION`
2. id 图元编号
3. ticket 逻辑时钟
4. name 分区名称
5. fileUuid 子图 UUID
6. path 分区形状

所有归属于分区的图元都得带上对应的分区图元编号

```json
{ "type": "VIA", "id":"viaUuid", "ticket": 1 }||
{
  "partitionId":"partitionId",// 带上该id,表示归属对应分区
  "groupId":0,
  "locked":1,
  "zIndex":3.223,
  "netName":"GND",
  "ruleName":"asdf",
  "centerX":100,
  "centerY":200,
  "holeDiameter":5,
  "viaDiameter":9,
  "viaType":0,
  "topSolderExpansion":null,
  "bottomSolderExpansion":null,
  "unusedInnerLayers":[17],
}|
```


---

### 基础图元格式

#### NET 配置网络信息

网络信息和 AD 的设计有区别

-   AD 这里是必选的
-   本格式只是需要有特殊 `网络类型` 和 `网络颜色` 才填写上去
    设计差异的原因在于是否有一个专门的网络设置界面，会列出所有网络去设置

```json
{ "type": "NET","id": "A", "ticket": 1 }||
{
  "netType":"High Speed",
  "specialColor":"#666666",
  "hideRetLine":0,
  "differentialName":"AASDF",
  "isPositiveNet":1,
  "equalLengthGroupName":"ABC",
}|
```

1. type 网络配置 `NET`
2. id 唯一标识，网络名称
3. ticket 逻辑时钟
4. netType 网络类型：`null` 为无类型
5. specialColor 特殊颜色：`null` 为无特殊颜色
6. hideRetLine 是否隐藏飞线
7. differentialName 差分对名称：`null` 为非差分对
8. isPositiveNet 是否差分对正极
9. equalLengthGroupName 等长组名称：`null` 为非等长组

```json
{ "type": "NET","id": "B", "ticket": 1 }||
{
  "netType":null,
  "specialColor":"#666666",
  "hideRetLine":1,
  "differentialName":"AASDF",
  "isPositiveNet":0,
  "equalLengthGroupName":"ABC",
}|
```

```json
{ "type": "NET","id": "C", "ticket": 1 }||
{
  "netType":"High Speed",
  "specialColor":null,
  "hideRetLine":1,
  "differentialName":null,
  "isPositiveNet":0,
  "equalLengthGroupName":null,
}|
```

#### PRIMITIVE 图元配置

```json
{ "type": "PRIMITIVE","id": "VIA", "ticket": 1 }||
{
  "display":1,
  "pick":0,
}|
```

1. type 图元配置 `PRIMITIVE`
2. id 唯一标识，图元名称
3. ticket 逻辑时钟
4. display 是否显示
5. pick 是否可拾取

```json
{ "type": "PRIMITIVE","id": "PAD", "ticket": 1 }||
{
  "display":0,
  "pick":1,
}|
```

#### GROUP 分组配置

通过分组配置，给每个组一个名称，如果无名称，由 EDA 决定如何显示默认组名

```json
{ "type": "GROUP","id": "2", "ticket": 1 }||
{
  "groupName":"ABCD",
}|
```

1. type 分组配置 `GROUP`
2. id 分组编号
3. ticket 逻辑时钟
4. groupName 名称

```json
{ "type": "GROUP","id": "5", "ticket": 1 }||
{
  "groupName":"中文名称",
}|
```

#### CONNECT 图元关联

图元关联用于表达一些图元的内部组合逻辑，例如

-   泪滴与 `LINE` `ARC` `PAD` `VIA` 的联系
-   焊盘与其相关的引脚 3D 外形 `FILL` 的联系
-   3D 外壳中 `CREASE` 与 `BOSS` `SHELL_ENTITY` 的联系
-   PCB 针对封装内图元的覆盖

只支持表达一对多的关系

```json
{ "type": "CONNECT","id": "e3", "ticket": 1 }||{ "relatedIds":["e15", "e18", "e100"] }|
```

1. type 图元关联 `CONNECT`
2. id 主图元编号
3. ticket 逻辑时钟
4. relatedIds 关联的图元编号

多对多的关系可以用多个表达，暂时没有对应场景

```json
{ "type": "CONNECT","id": "e4", "ticket": 1 }||{ "relatedIds":["e5", "e6"] }|
```

```json
{ "type": "CONNECT","id": "e5", "ticket": 1 }||{ "relatedIds":["e4", "e6"] }|
```

```json
{ "type": "CONNECT","id": "e6", "ticket": 1 }||{ "relatedIds":["e4", "e5"] }|
```

PCB 中针对封装内图元的覆盖，使用形如 `/^[a-z]+\d+[a-z]+\d+$/i` 的形式将封装和里面图元 ID 拼一起来引用，表达如下

```json
["DOCTYPE", "PCB", "1.0"]
["COMPONENT", "e13", 5, 1, ...]
["VIA", "e13e20", 0, "GND", "asdf", ....]
["PAD", "e13e25", 1, "GND", 0, "1", ....]
["CONNECT", "e13", ["e13e20", "e13e25"]]
```

封装内容如下

```json
["DOCTYPE", "FOOTPRINT", "1.0"]
["VIA", "e20", 0, "GND", "sss", ....]
["PAD", "e25", 1, "GND", 0, "3", ....]
```

#### PROP 附加图元属性

有一些图元属性非必须表达，且相对通用，则用 `PROP` 辅助描述

```json
{ "type": "PROP", "id":"UUID", "ticket": 1 }||{ "color":"#22ee44" }|
```

1. type 附加图元属性：PROP
1. id 被附加的图元编号：有两种编码形式
    1. 普通编号：形式为 `/^[a-z]+\d+$/i`，如 `e1` `e123` 等
    1. 封装实例编号：形式为 `/^[a-z]+\d+[a-z]+\d+$/i`，用于如封装中的丝印等，如 `e1e5` `e12e22` 等
1. color 特殊颜色

```json
{ "type": "PROP", "id":"e7e25", "ticket": 1 }||{ "color":"#22ee44" }|
```

如果要覆盖封装内的图元，则采用此复合编号的形式去描述

#### EQLEN_GRP

```json
{ "type": "EQLEN_GRP", "id":"UUID", "ticket": 1 }||
{
  "name":"equal length pad group 2",
  "sort":1.97,
  "pads":
    [
        ["U1:1", "U2:3"],
        ["U1:2", "U1:a"]
    ],
}|
```

1. type 等长组：`EQLEN_GRP`
2. id 唯一编号
3. ticket 逻辑时钟
4. name 等长组名称：全工程唯一
5. sort 排序
6. pads 用 `位号:焊盘编号` 标识焊盘的数组


---

### 焊盘与过孔

#### VIA 过孔

过孔一般用于打通不同层之间的电路
对于多层板一般有如下几种模式
通孔：`开始层` 到 `结束层` 贯穿顶层底层
盲孔：`开始层` 或 `结束层` 只有一个属于顶层或底层
埋孔：`开始层` 和 `结束层` 都不属于顶层或底层

```json
{ "type": "VIA", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":3.223,
  "netName":"GND",
  "ruleName":"asdf",
  "centerX":100,
  "centerY":200,
  "holeDiameter":5,
  "viaDiameter":9,
  "viaType":0,
  "topSolderExpansion":null,
  "bottomSolderExpansion":null,
  "unusedInnerLayers":[17],
}|
```

1. type 过孔 `VIA`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName NET
9. ruleName 过孔层类型：设计规则名称，定义过孔的开始层结束层
10. centerX 坐标 X
11. centerY 坐标 Y
12. holeDiameter 孔直径
13. viaDiameter 焊盘直径
14. viaType 过孔类型：0 普通过孔 1 缝合孔
15. topSolderExpansion 顶层阻焊扩展：`null` 为遵循规则
16. bottomSolderExpansion 底层阻焊扩展：`null` 为遵循规则
17. unusedInnerLayers 隐藏焊盘层（可选）：被隐藏焊盘的层数组

#### PAD 焊盘

焊盘一般用于元器件与电路板焊接
焊盘要么贯穿整个电路板，要么只在顶层或者底层，所以只有顶层、底层、多层三种层

```json
{ "type": "PAD", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":6.234,
  "netName":"GND",
  "layerId":0,
  "num":"1",
  "centerX":100,
  "centerY":200,
  "padAngle":15,
  "hole":["参考孔"],
  "defaultPad":["参考焊盘"],
  "specialPad":[[0, 1, ["参考焊盘"]]],
  "padOffsetX":10,
  "padOffsetY":-5,
  "relativeAngle":30,
  "plated":1,
  "padType":null,
  "topSolderExpansion":0.5,
  "bottomSolderExpansion":0.4,
  "topPasteExpansion":null,
  "bottomPasteExpansion":0,
  "connectMode":0,
  "spokeSpace":10,
  "spokeWidth":5,
  "spokeAngle":45,
  "unusedInnerLayers":[15, 17],
}|
```

1. type 焊盘 `PAD`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层（只有顶层、底层、多层）
10. num 焊盘编号
11. centerX 焊盘原点 X
12. centerY 焊盘原点 Y
13. padAngle 焊盘旋转角度
14. hole 孔：参考孔，`null` 表示无孔
15. defaultPad 默认焊盘：参考焊盘
16. specialPad 特殊焊盘（多组，每组定义如下）
    1. 开始层
    2. 结束层
    3. 参考焊盘
17. padOffsetX 孔偏移 X
18. padOffsetY 孔偏移 Y
19. relativeAngle 孔相对焊盘旋转角度
20. plated 是否金属化孔壁
21. padType 焊盘功能：0 普通焊盘 1 测试点 2 标识点
22. topSolderExpansion 顶层阻焊扩展：`null` 为遵循规则
23. bottomSolderExpansion 底层阻焊扩展：`null` 为遵循规则
24. topPasteExpansion 顶层助焊扩展：`null` 为遵循规则
25. bottomPasteExpansion 底层助焊扩展：`null` 为遵循规则
26. connectMode 热焊-连接方式：`null` 为遵循规则，其他数据定义同设计规则
27. spokeSpace 热焊-发散间距：`null` 为遵循规则，其他数据定义同设计规则
28. spokeWidth 热焊-发散线宽：`null` 为遵循规则，其他数据定义同设计规则
29. spokeAngle 热焊-发散角度：`null` 为遵循规则，其他数据定义同设计规则
30. unusedInnerLayers 隐藏焊盘层（可选）：被隐藏焊盘的层数组

##### 孔

1. 长圆孔
    1. 长圆孔 `ROUND`
    1. 宽
    1. 高
1. 方孔
    1. 方孔 `RECT`
    1. 宽
    1. 高

##### 焊盘

孔的旋转与盘的互相独立

1. 长圆焊盘
    1. 长圆焊盘 `ROUND`
    1. 宽
    1. 高
1. 方焊盘
    1. 方焊盘 `RECT`
    1. 宽
    1. 高
    1. 圆角半径
1. 正多边形焊盘
    1. 正多边形焊盘 `NGON` （名称来自 3DSMAX）
    1. 直径
    1. 边数（> 2）
1. 多边形焊盘
    1. 多边形焊盘 `POLY`
    1. 参考复杂多边形，以孔为原点的相对位置


---

### 形状图元

#### LINE 直线

```json
{ "type": "LINE", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":9.223,
  "netName":"GND",
  "layerId":1,
  "startX":100,
  "startY":200,
  "endX":400,
  "endY":300,
  "width":0.7,
}|
```

1. type 直线 `LINE`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层
10. startX 开始 X
11. startY 开始 Y
12. endX 结束 X
13. endY 结束 Y
14. width 线宽

#### ARC/CARC 圆弧线

圆弧借鉴 Eagle 的数学模型，以 `起始` `结束` 为基准去描述

```json
{ "type": "ARC", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":2.866,
  "netName":"GND",
  "layerId":1,
  "startX":100,
  "startY":200,
  "endX":300,
  "endY":400,
  "angle":-170,
  "width":10,
}|
```

1. type 圆弧线
    - `ARC` 两点交互模式
    - `CARC` 中心圆弧交互模式
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层
10. startX 起始 X
11. startY 起始 Y
12. endX 结束 X
13. endY 结束 Y
14. angle 圆弧角，逆时针正，顺时针负
15. width 线宽

#### 多边形体系

SVG 中 path 是一个对多边形优秀的抽象。
但由于 PCB 内用不到其中相对位置等功能，并且有条件设计更方便解析的方式。
所以仿造 SVG 的 path 创造了一种类似的表达多边形的方式。
多边形体系内 `POLY` `REGION` `POUR` 支持互相转换

##### 单多边形的定义

单多边形为首尾重合的一条不间断的线所描述的区域。如果首尾不重合需要将其自动重合。

```json
[300, 200, "L", 400, 200, "ARC", 400, 220, 15, "C", 200, 500, 400, 300, 100, 100]
```

```json
["R", 100, 200, 300, 300, 0]
```

```json
["CIRCLE", 100, 200, 5, 1]
```

###### L 直线模式

`X Y L X Y X Y ...` 模式为直线模式，所有坐标将用直线将其连一一连起来

###### ARC/CARC 圆弧模式

`startX startY ARC angle endX endY` 模式为圆弧模式

-   startX/startY 开始坐标
-   angle：圆弧角，逆时针正，顺时针负
-   endX/endY 结束坐标

`startX startY CARC angle endX endY` 中心圆弧交互模式

###### C 三阶贝塞尔模式

`X1 Y1 C X2 Y2 X3 Y3 X4 Y4 ...` 模式为三阶贝塞尔模式，所有坐标为其控制点

###### R 矩形模式

`R X Y width height rot isCCW round` 矩形模式与其它都不兼容，是一个独立的模式

-   X/Y：左上坐标
-   width：宽
-   height：高
-   rot：旋转角度
-   isCCW：是否逆时针
-   round：圆角半径

###### CIRCLE 圆形模式

`CIRCLE cx cy r isCCW` 圆形模式与其它都不兼容，是一个独立的模式

-   cx/cy：中心点坐标
-   r：半径
-   isCCW：是否逆时针

##### 复杂多边形的定义

```json
[[单多边形1：外框], [单多边形2：内洞]]
```

复杂多边形可以包含多个单多边形，固定第一个多边形顺时针，表示外框，后续所有多边形逆时针，表示内洞

#### POLY 折线

折线和 `LINE` `ARC` 区别较小，但是其具有保持绘制时【连续的一条线】的概念，以和 `REGION` `FILL` `POUR` 互转

```json
{ "type": "POLY", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":6.417,
  "netName":"GND",
  "layerId":1,
  "width":0.5,
  "path":["单多边形"],
}|
```

1. type 折线 `POLY`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层
10. width 线宽
11. path 请参考单多边形

#### FILL 填充

```json
{ "type": "FILL", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex": 0.794,
  "netName":"GND",
  "layerId":3,
  "width":10,
  "fillStyle":0,
  "path":["复杂多边形"],
}|
```

1. type 填充 `FILL`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层
10. width 线宽
11. fillStyle 填充模式：0 实心填充 1 网格填充 2 内电层填充
12. path 请参考复杂多边形章节
    1. 对于单次画的多边形，这里只有一个单多边形
    1. 对于组合模式画的多边形，这里才有多个单多边形

#### REGION 区域

禁止区域为未来一个很重要的功能，除了辅助手工设计以外，还可以辅助自动布局布线，提供自动化工具除设计规则外，区域范围的约束信息

```json
{ "type": "REGION", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.901,
  "layerId":3,
  "width":1,
  "prohibitType":[1, 2, 5],
  "path":["复杂多边形"],
  "name":"aa constraint",

}|
```

1. type 区域 `REGION`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层
9. width 线宽
10. prohibitType 禁止类型，可同时存在多个
    1. \*禁止布线与放置填充区域（弃用，但解析要做兼容）
    2. 禁止元件
    3. 禁止过孔
    4. \*禁止覆铜与内电层（弃用，但解析要做兼容）
    5. 禁止布线
    6. 禁止放置填充区域
    7. 禁止覆铜
    8. 禁止内电层
11. path 请参考复杂多边形章节
    1. 对于单次画的多边形，这里只有一个单多边形
    2. 对于组合模式画的多边形，这里才有多个单多边形
12. name 名称（可选）

#### POUR 覆铜边框

和之前的覆铜有一个实质性的差别在于，支持复杂多边形
也就是说覆铜区域可以含洞，理论上可以实现文字路径转出来的多边形作为覆铜区域
覆铜按照其在格式内出现的顺序覆铜

```json
{ "type": "POUR", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.779,
  "netName":"GND",
  "layerId":1,
  "width":1,
  "name":"TOPGND",
  "order":4,
  "path":["复杂多边形"],
  "pourType":["覆铜类型"],
  "keepIsland":1,
}|
["POUR", "e100", 5, 1, 0.779, "GND", 1, 1, "TOPGND", 4, 复杂多边形, [覆铜类型], 1]
```

1. type 覆铜 `POUR`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. netName 网络
9. layerId 层
10. width 线宽
11. name 覆铜名称
12. order 覆铜优先级
13. path 请参考复杂多边形章节
14. pourType 请参考覆铜类型
15. keepIsland 是否保留孤岛

###### 覆铜类型

###### SOLID 实心填充

```json
["SOLID", 2]
```

1. 实心填充 `SOLID`
1. 最小覆铜细度（生产优化用，AD 里的 Neck），0 为不开启生产优化

```json
["POUR", "e100", "GND", 1, "BOTGND", 2, 复杂多边形, ["SOLID", 2], 1, 0]
```

```json
["POUR", "e100", "GND", 1, "BOTGND", 2, 复杂多边形, ["SOLID", 0], 1, 0]
```

###### LINE 线填充

```json
["LINE", 0, 0, 10, 20]
```

1. 线填充 `LINE`
1. 填充模式：`0` 网格填充 `1` 水平线填充 `2` 垂直线填充
1. 旋转角度
1. 线宽
1. 线距

```json
["POUR", "e100", "GND", 1, "", 9, 复杂多边形, ["LINE", 0, 0, 10, 20], 0.6, 1, 0, 0]
```

#### POURED 覆铜结果

```json
{ "type": "POURED", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "targetId":"e100",
  "strokeWidth":0,
  "fill":1,
  "path":["复杂多边形"],
}|
```

1. type 覆铜结果 `POURED`
2. id 图元编号
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. targetId 所属覆铜边框 `POUR` 编号
6. strokeWidth 描边线宽：0 为不描边
7. fill 是否填充
8. path 路径, 复杂多边形

#### IMAGE 图片

`IMAGE` 和 `REGION` 极为类似，但是在操作上，`IMAGE` 不存在控制点，不能自由改变其形态，只能进行整体性的放大、缩小、旋转、翻转、平移等操作
当 `IMAGE` 在信号层时，在 DRC 视角下，为一个无网络的，由 `起始` `结束` `旋转角度` `是否镜像` 定义的矩形区域

```json
{ "type": "IMAGE", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":5.5464,
  "layerId":31,
  "startX":200,
  "startY":200,
  "width":400,
  "height":400,
  "angle":45,
  "mirror":1,
  "path":["复杂多边形"],
}|
```

1. type 图片 `IMAGE`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层
9. startX 左上 X
10. startY 左上 Y
11. width 宽
12. height 高
13. angle 旋转角度，绕 `起始` 点
14. mirror 原始图片是否水平镜像，镜像以原始图片 BBox 中点进行水平镜像
15. path 多个复杂多边形，请参考复杂多边形章节，这里存储的是原始数据，整个生命周期不需要调整

#### TEARDROP 泪滴

泪滴不可选中，不可直接操作，当关联的任意图元发生变化时，EDA 应让其自动消失

```json
{ "type": "TEARDROP", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "netName":"GND",
  "layerId":3,
  "path":"简单多边形",
  "groupId":0,
}|
```

1. type 泪滴 `TEARDROP`
2. id 编号
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. netName 网络
6. layerId 层
7. path 简单多边形
8. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组

#### FPC_FILL 柔性工艺补强板

```json
{ "type": "FPC_FILL", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":7.7845,
  "layerId":3,
  "material":"3M468",
  "thickness":7.874,
  "path":"复杂多边形",
}|
```

1. type 填充 `FPC_FILL`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层
9. material 材质
    - PI
    - STEEL
    - FR4
    - 3M468
    - 3M9077
    - EMI_Shielding_Film
10. thickness 厚度（注意单位和其它图元一致）
11. path 请参考复杂多边形章节


---

### OBJ 二进制内嵌对象

将文件等数据编码到如下图元，以内嵌于图页上的图片和文件，可作为附件下载，以及直接显示

```json
{ "type": "OBJ", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":5.286,
  "layerId":15,
  "fileName":"a.png",
  "startX":200,
  "startY":300,
  "width":10,
  "height":20,
  "angle":0,
  "mirror":1,
  "path":"blob:1234ade2f",
}|
["OBJ", "e662", 0, 1, 5.286, 15, "a.png", 200, 300, 10, 20, 0, 1, "blob:1234ade2f"]
```

1. type 二进制内嵌对象标识：OBJ
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层
9. fileName 文件名
10. startX 左上 X
11. startY 左上 Y
12. width 宽
13. height 高
14. angle 旋转角度，绕 `左上` 点
15. mirror 原始图片是否水平镜像，镜像以原始图片 BBox 中点进行水平镜像
16. path 二进制数据
    1. 一般格式，与 `Data Urls` 完全兼容 `data:[<mediatype>][;base64],<data>`
        - 如 `data:image/png;base64,asdfasdfwer`
    1. BLOB 引用格式 `blob:hashid`


---

### 3D 外壳体系

#### SHELL 外壳

```json
{ "type": "SHELL", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.223,
  "shellType":"T&B",
  "shellHeight":50,
  "pcbHeight":100,
  "strokeWidth":10,
  "path":"复杂多边形",
  "attrs":{},
}|
```

1. type 外壳 `SHELL`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. shellType 外壳类型
    - `T&B` 上下壳
    - `DRAWER` 推盖
    - `CLAM` 翻盖
9. shellHeight 外壳高度
10. pcbHeight PCB 高度
11. strokeWidth \*线宽（弃用）
12. path 参考复杂多边形
13. attrs 自定义属性：3D 外壳每种类型都有一组自定义的外壳属性
    - T&B
        - Thickness：外壳厚度
        - BottomHeight：下壳高度
        - TopInnerHeight：上壳内壁高度
        - TopInnerThickness：上壳内壁厚度
    - DRAWER
        - Thickness：外壳厚度
        - Direction：推盖方向
            - `1` X 轴正向
            - `2` X 轴负向
            - `4` Y 轴正向
            - `8` Y 轴负向

#### CREASE 侧面基准线（折痕）

`CREASE` 折痕图元需要配合 `CONNECT` 关联 `SHELL_ENTITY` `BOSS`，以表达外壳挖槽

```json
{ "type": "CREASE", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":8.275,
  "layerId":49,
  "startX":20,
  "startY":30,
  "endX":40,
  "endY":50,
  "angle":90,
}|
```

1. type 折痕 `CREASE`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层编号
9. startX 起始 X
10. startY 起始 Y
11. endX 结束 X
12. endY 结束 Y
13. angle 折叠角度

#### SHELL_ENTITY 外壳实体区域

没有通过 `CONNECT` 与 `CREASE` 关联的 `SHELL_ENTITY`，则垂直进行顶层、底层外壳的填充/裁剪（挖槽）

```json
{ "type": "SHELL_ENTITY", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.15,
  "layerId":50,
  "booleanOperation":0,
  "belong":0,
  "depth":10,
  "strokeWidth":10,
  "path":"复杂多边形",
}|
```

1. type 外壳实体区域 `SHELL_ENTITY`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层编号
9. booleanOperation 布尔操作：0 裁剪 1 填充
10. belong 隶属：0 自动 1 上壳 2 下壳 4 外壳边框 8 螺丝柱 16 实体，可通过加法组合
    - 上下壳：3 = 1 + 2
    - 外壳边框 + 螺丝柱：12 = 4 + 8
    - 螺丝柱 + 实体：24 = 8 + 16
    - 外壳边框 + 螺丝柱 + 实体：28 = 4 + 8 + 16
11. depth 深度
12. strokeWidth 线宽
13. path 参考复杂多边形

#### BOSS 螺丝柱

通过 `CONNECT` 与 `CREASE` 关联的 `BOSS`，添加的是侧方的螺丝柱

```json
{ "type": "BOSS", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.8976,
  "layerId":50,
  "centerX":100,
  "centerY":200,
  "specification":"M2",
  "depth":100,
  "thickness":10,
  "holeDiameter":20,
  "counterBore":[100, 20],
  "stiffener":[20, 40, 20, 10],
}|
```

1. type 螺丝柱 `BOSS`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层编号
9. centerX 中心 X
10. centerY 中心 Y
11. specification 螺丝型号：`"M2"` `"M3"` 等，或者 `null` 表示自定义
12. depth 螺丝柱高度
13. thickness 螺丝柱壁厚
14. holeDiameter 螺丝柱通孔直径
15. counterBore 沉头所需参数，当此参数为 `null` 时表示不需要沉头
    - 螺丝头高度
    - 螺丝头直径
16. stiffener 加强筋所需参数，当此参数为 `null` 时表示不需要加强筋
    - 加强筋上端宽度
    - 加强筋下端宽度
    - 加强筋距螺丝柱上端距离
    - 加强筋厚度


---

### 文字体系

#### STRING 文字

当 `STRING` 在信号层时，在 DRC 视角下，为

1. 无网络的
1. `位置` 为 0,0， `旋转角度` 为 0， `是否镜像` 为 0 的 BBox
1. 进行 `位置` `旋转角度` `是否镜像` 变换后的矩形区域

```json
{ "type": "STRING", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.2693,
  "layerId":1,
  "positionX":300,
  "positionY":600,
  "text":"我人人有的的和我",
  "fontFamily":"宋体",
  "fontSize":50,
  "strokeWidth":10,
  "bold":0,
  "italic":0,
  "origin":5,
  "angle":15,
  "reverse":1,
  "reverseExpansion":0,
  "mirror":1,
  "width":100,
  "height":200,
  "path":["复杂多边形"],
}|
["STRING", "e100", 0, 1, 0.2693, 1, 300, 600, "我人人有的的和我", "宋体", 50, 10, 0, 0, 5, 15, 1, 0, 1]
```

1. type 文字 `STRING`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层
9. positionX 位置 X
10. positionY 位置 Y
11. text 内容
12. fontFamily 字体名称
13. fontSize 字号
14. strokeWidth 粗细
15. bold 是否加粗
16. italic 是否斜体
17. origin 对齐模式 `0` 左顶 `1` 中顶 `2` 右顶 `3` 左中 `4` 中中 `5` 右中 `6` 左底 `7` 中底 `8` 右底
18. angle 旋转角度
19. reverse 是否反相扩展
20. expansion 反相扩展尺寸：反相扩展区域的尺寸，支持负数
21. mirror 是否镜像，一般来说，当一个文字出现在底层，这里也需要相应调整成 `1`
22. width 高，没有则为 null
23. height 宽，没有则为 null
24. path 复杂多边形数组，没有则为 null


---

### ATTR 属性

属性用以描述 PCB 或者 FOOTPRINT 有可能需要在图上显示的属性

```json
{ "type": "ATTR", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.072,
  "parentId":"",
  "layerId":1,
  "positionX":200,
  "positionY":150,
  "key":"DESIGNATOR",
  "value":"U1",
  "keyVisible":0,
  "valueVisible":1,
  "fontFamily":"宋体",
  "fontSize":50,
  "strokeWidth":10,
  "bold":0,
  "italic":0,
  "origin":5,
  "angle":15,
  "reverse":1,
  "reverseExpansion":0,
  "mirror":1,
  "width":100,
  "height":200,
  "path":["复杂多边形"],
}|
```

1. type 属性 `ATTR`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. parentId 隶属编号，留空表示当前块级图元
9. layerId 层
10. positionX 位置 X：未显示过的属性位置固定为 null
11. positionY 位置 Y：未显示过的属性位置固定为 null
12. key Key
13. value Value
14. keyVisible 是否显示 Key
15. valueVisible 是否显示 Value
16. fontFamily 字体名称
17. fontSize 字号
18. strokeWidth 粗细
19. bold 是否加粗
20. italic 是否斜体
21. origin 对齐模式 `0` 左顶 `1` 中顶 `2` 右顶 `3` 左中 `4` 中中 `5` 右中 `6` 左底 `7` 中底 `8` 右底
22. angle 旋转角度
23. reverse 是否反相扩展
24. reverseExpansion 反相扩展尺寸：反相扩展区域的尺寸，支持负数
25. mirror 是否镜像，一般来说，当一个文字出现在底层，这里也需要相应调整成 `1`
26. width 高，没有则为 null
27. height 宽，没有则为 null
28. path 复杂多边形数组，没有则为 null


---

### DIMENSION 尺寸工具集

```json
{ "type": "DIMENSION", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":2.221,
  "type":"RADIUS",
  "layerId":3,
  "unit":"mm",
  "strokeWidth":0.5,
  "precision":3,
  "textFollow":1,
  "coords": [100 200 300 400 400 400],
}|
["DIMENSION", "e101", 0, 1, 2.221, "RADIUS", 3, "mm", 0.5, 3, 1, [100 200 300 400 400 400]]
```

1. type 尺寸工具 `DIMENSION`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. type 尺寸类型： `RADIUS` 半径 `LENGTH` 长度 `ANGLE` 角度
9. layerId 层
10. unit 单位 `mm` `cm` `inch` `mil`
11. strokeWidth 线宽
12. precision 精度
13. textFollow 文字是否跟随：`1` 工具自动决定文字的位置 `0` 永远采用 `ATTR` 的位置
14. coords 坐标集 X1 Y1 X2 Y2 X3 Y3 ... 不同尺寸类型对坐标有不同的定义

```json
["ATTR", "e102", 0, "e101", 1, 200, 150, "VALUE", "1234mm", 0, 1, "宋体", 50, 10, 0, 0, 0, 2, 15, 1, 1]
```

`DIMENSION` 需要附带一个 Key 为 `VALUE` 的属性，表达尺寸工具的文字部分，EDA 需要忽略其不需要的属性，例如 `是否显示 Key` `是否显示 Value`

##### RADIUS 半径工具

坐标集第一个坐标为和 ARC 接触的端点，最后一个坐标为默认显示文字的端点，如下图

![图片](../public/storage/images/cn/format/pcb/dimension/dim_radius.png)

##### LENGTH 长度工具

坐标集只需要具有四个点，分别如下图

![图片](../public/storage/images/cn/format/pcb/dimension/dim_length.png)

##### ANGLE 角度工具

坐标集需要 3 个点，分别如下图

![图片](../public/storage/images/cn/format/pcb/dimension/dim_angle.png)


---

### 封装体系

PCB 的封装采用 Master/Instance 模式，通过覆盖规则去描述封装，以最低冗余的方式去描述

#### COMPONENT 器件实例

实例里只存在属性，同 `Key` 属性将以实例的代替 `FOOTPRINT` 的

```json
{ "type": "COMPONENT", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.22,
  "layerId":1,
  "positionX":150,
  "positionY":200,
  "angle":45,
  "attrs":{ "3D Model": "uuid", "3D Model Transform": "20,10,0,0,15,45,0,0,20" },
}|
```

1. type 实例 `COMPONENT`
2. id 图元编号, 文档内唯一
3. ticket 逻辑时钟
4. partitionId 所属分区编号，为 null 表示无分区，封装忽略该字段
5. groupId 分组编号：0 不分组，非 0 为组标志，相同组标志的为一组
6. locked 是否锁定
7. zIndex Z 轴高度
8. layerId 层（只有顶层底层）
9. positionX 位置 X
10. positionY 位置 Y
11. angle 旋转角度
12. attrs 自定义属性
    - 固定 `3D Model` 为 3D 模型的 uuid，此 uuid 代表 components 表中 doctype = 16 的一条记录
    - 固定 `3D Model Transform` 为 3D 模型变换参数

```json
["ATTR", "e102", 0, "e8", 1, "Designator", "U1", 0, 1, "宋体", 50, 10, 0, 0, 0, 1, 2, 15, 1, 1]
```

```json
["ATTR", "e103", 0, "e8", 1, "Footprint", "footprint-uuid", 0, 1, "宋体", 50, 10, 0, 0, 0, 1, 2, 15, 1, 1]
```

```json
["ATTR", "e104", 0, "e8", 1, "Device", "device-uuid", 0, 1, "宋体", 50, 10, 0, 0, 0, 1, 2, 15, 1, 1]
```

###### 焊盘实例网络映射 `PAD_NET`

```json
{ "type": "PAD_NET", "id":"COMPONENT_UUID", "ticket": 1 }||
{
  "padNum":"a1",
  "padNet":"GND",
  "padId":"e125",
}|
```

1. type 焊盘实例网络映射 `PAD_NET`
2. id 所属器件实例编号
3. ticket 逻辑时钟
4. padNum 焊盘编号
5. padNet 网络名
6. padId 封装内焊盘 ID（可选）

###### 复用图块信息 `REUSE_BLOCK`

```json
{ "type": "REUSE_BLOCK", "id":"COMPONENT_UUID", "ticket": 1 }||
{
  "groupId":"$1e16",
  "channelId":"$2e5_$4e3",
}|
```

1. type 复用图块信息 `REUSE_BLOCK`
2. id 所属器件实例编号
3. groupId 分组 ID
4. channelId 通道 ID

###### 3D Model Transform 的特殊说明

```json
{ "type": "COMPONENT", "id":"UUID", "ticket": 1 }||
{
  "partitionId":null,
  "groupId":0,
  "locked":1,
  "zIndex":0.22,
  "layerId":1,
  "positionX":150,
  "positionY":200,
  "angle":45,
  "attrs":{ "3D Model": "uuid", "3D Model Transform": "20,10,0,0,15,45,0,0,20" },
}|
```

在器件中，固定 `3D Model Transform` 为 3D 模型为匹配此器件【在顶层】【坐标 0,0】【旋转角度 0】时所需要的变换参数

其参数为

1. sizeX：X 轴尺寸
2. sizeY：Y 轴尺寸
3. sizeZ：Z 轴尺寸，这里有个兼容性处理，如果为 0，则自动适应高度
4. rotZ：绕 Z 轴旋转角度
5. rotX：绕 X 轴旋转角度
6. rotY：绕 Y 轴旋转角度
7. offX：X 轴偏移量
8. offY：Y 轴偏移量
9. offZ：Z 轴偏移量

通过 3D 模型变换参数，生成 3D 模型变换矩阵的算法如下

```python
cx = 3D 模型 X 轴中点
cy = 3D 模型 Y 轴中点
bz = 3D 模型 最低 Z 值

wx = 3D 模型 X 轴宽度
wy = 3D 模型 Y 轴宽度
wz = 3D 模型 Z 轴宽度

ORIGIN = translate(-cx, -cy, -bz)
SCALE = scale(sizeX / wx, sizeY / wy, sizeZ / wz)
ROT = rotateZXY(rotZ, rotX, rotY)
OFFSET = translate(offX, offY, offZ)

MATRIX = OFFSET X ROT X SCALE X ORIGIN
```


---

### 设计规则体系

#### 设计规则模板

设计规则模板逻辑上有两种理解方式，PCB 自行根据需要选择

1. 作为其他设计规则的基版，其他设计规则是对模板的覆盖
1. 与其他设计规则互斥，有模板其他设计规则只是暂存，不产生任何效果
1. 作为来源于哪个模板的标识，不影响后续实际规则的效力（当前采用的方案）

```json
{ "type": "RULE_TEMPLATE", "ticket": 1 }||{ "name": "JLCPCB Capability(High Frequency Board)"}|
```

1. type 设计规则模板：`RULE_TEMPLATE`
2. ticket 逻辑时钟
3. name 模板名称

#### 设计规则

```json
{ "type": "RULE", "id":"UUID", "ticket": 1 }||
{
  "ruleType":"Safe Clearance",
  "ruleName":"通用",
  "ruleState":1,
  "ruleContext":{},
}|
```

1. type 设计规则：`RULE`
2. ticket 逻辑时钟
3. ruleType 规则类型：EDA 自己决定
4. ruleName 规则名称
5. ruleState 规则状态：0 普通规则 1 默认规则 2 停用规则
6. ruleContext 规则内容：EDA 自己决定

同一 `规则类型` 设计规则出现的顺序，需要和【规则管理】左侧树的顺序一致

#### 规则选择器

```json
{ "type": "RULE_SELECTOR", "id":"UUID", "ticket": 1 }||
{
  "ruleSelect":["NET", "GND"],
  "ruleOrder":0,
  "ruleKeyValue":{ "Safe Clearance": "通用", "Other Clearance": "通用" },
}|
```

1. type 规则选择器：`RULE_SELECTOR`
2. ticket 逻辑时钟
3. ruleSelect 选择器
    1. 网络类型：`["NET_CLASS", "High Speed"]`
    2. 网络：`["NET", "GND"]`
    3. 层：`["LAYER", 3]`
    4. 区域：`["REGION", "e10"]`
    5. 封装：`["FOOTPRINT", "0805"]`
    6. 元件：`["COMPONENT", "e100"]`
    7. 覆铜：`["POUR", "e100"]`
    8. 差分对：`["DIFF_PAIR", "asdf"]`
    9. 等长对：`["EQ_LEN_GRP", "fdsa"]`
    10. 未来如果要配置逻辑，可以写逻辑 `["AND", ["NET", "GND"], ["LAYER", 5]]`
4. ruleOrder 优先级：数值越小，优先级越高，建议
    - `0` 元件规则
    - `1` 封装规则
    - `2` 区域规则
    - `3` 网络-网络规则
    - `4` 网络规则
    - `5` 层规则
5. ruleKeyValue 规则，Key 为 规则类，Value 为 规则名称，每个规则类下只能选择一个规则


---

### 拼版

```json
{ "type": "PANELIZE", "ticket": 1 }||
{
  "on":1,
  "row":2,
  "column":3,
  "rowSpacing":5.5,
  "columnSpacing":6.1,
  "onlyOutline":1,
}|
```

1. type 拼版 `PANELIZE`
2. ticket 逻辑时钟
3. on 是否启用
4. row 行数
5. column 列数
6. rowSpacing 行距
7. columnSpacing 列距
8. onlyOutline 是否只拼边框

```json
{ "type": "PANELIZE_STAMP", "ticket": 1 }||
{
  "direction":1,
  "on":1,
  "stampHoleGroupQuantity":3,
  "stampHoleDiameter":8,
  "stampHoleQuantityPerGroup":0,
  "stampHoleSpacing":1,
}|
```

1. type 邮票孔参数：`PANELIZE_STAMP`
2. ticket 逻辑时钟
3. direction 方向：`0` 水平 `1` 垂直
4. on 是否启用（不启用则使用 V-CUT）
5. stampHoleGroupQuantity 邮票孔组数
6. stampHoleDiameter 邮票孔直径
7. stampHoleQuantityPerGroup 邮票孔每组数量
8. stampHoleSpacing 邮票孔间距

```json
{ "type": "PANELIZE_SIDE", "ticket": 1 }||
{
  "direction":0,
  "on":1,
  "sideHeight":5,
  "positionHoleDiameter":3,
  "markDiameter":2,
  "markExpansion":1,
}|
```

1. type 工艺边参数：`PANELIZE_SIDE`
2. ticket 逻辑时钟
3. direction 方向：`0` 水平 `1` 垂直
4. on 是否启用（不启用则不使用工艺边）
5. sideHeight 工艺边高度
6. positionHoleDiameter 定位孔直径（`0` 表示无定位孔）
7. markDiameter Mark 点直径（`0` 表示不启用 Mark 点）
8. markExpansion Mark 点阻焊扩展

