-- ======================================================================
-- 客商录入模块 DDL 迁移脚本
--
-- 用途：
--   1. 为 merchant_basic_info 表新增客商录入模块所需的业务字段
--   2. 创建 merchant_contact（客商对接人信息表）
--   3. 创建 merchant_attachment（客商附件表）
--
-- 执行方式：
--   mysql -u <user> -p <database_name> < migration.sql
--
-- 注意：
--   - 执行前请确认 merchant_basic_info 表已存在
--   - 若已有历史数据，tax_number 和 legal_person 为 NOT NULL，
--     需先为历史数据填充默认值（如 'UNKNOWN'）再执行 ALTER TABLE
--   - 建议在测试环境验证后再上生产
-- ======================================================================

-- ------------------------------------------------------------------
-- 1. 为 merchant_basic_info 表新增业务字段（三步法，兼容历史数据）
-- ------------------------------------------------------------------

-- ======================================================================
-- 第一步：添加新列（允许 NULL，不设默认值，避免锁表过久）
-- ======================================================================
ALTER TABLE merchant_basic_info
ADD COLUMN tax_number VARCHAR(18) NULL UNIQUE COMMENT '统一社会信用代码',
ADD COLUMN legal_person VARCHAR(64) NULL COMMENT '法定代表人',
ADD COLUMN registered_address VARCHAR(256) NULL COMMENT '注册地址',
ADD COLUMN actual_controller VARCHAR(64) NULL COMMENT '实际控制人',
ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '建档时间',
ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间';

-- ======================================================================
-- 第二步：为已有的历史数据填充默认值（防止后续改为 NOT NULL 时报错）
-- 注：若确定无历史数据，此步骤不会影响任何行，但执行无害
-- ======================================================================
UPDATE merchant_basic_info 
SET tax_number = CONCAT('UNKNOWN_', merchant_id) 
WHERE tax_number IS NULL;

UPDATE merchant_basic_info 
SET legal_person = 'UNKNOWN' 
WHERE legal_person IS NULL;

-- ======================================================================
-- 第三步：修改为 NOT NULL（此时所有行均有值，执行安全）
-- ======================================================================
ALTER TABLE merchant_basic_info
MODIFY COLUMN tax_number VARCHAR(18) NOT NULL,
MODIFY COLUMN legal_person VARCHAR(64) NOT NULL;


-- ------------------------------------------------------------------
-- 2. 创建客商对接人信息表
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_contact (
    id INT PRIMARY KEY AUTO_INCREMENT,
    merchant_id BIGINT NOT NULL,
    name VARCHAR(64) NOT NULL,
    position VARCHAR(64) NULL,
    phone VARCHAR(32) NULL,
    email VARCHAR(128) NULL,
    business_role VARCHAR(32) NULL COMMENT '业务职责，如：业务对接人、财务对接人',
    is_primary TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否主联络人：1=是，0=否',
    remark VARCHAR(256) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_merchant_id (merchant_id),
    FOREIGN KEY (merchant_id) REFERENCES merchant_basic_info(merchant_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ------------------------------------------------------------------
-- 3. 创建客商附件表
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_attachment (
    id INT PRIMARY KEY AUTO_INCREMENT,
    merchant_id BIGINT NOT NULL,
    file_name VARCHAR(256) NOT NULL COMMENT '原始文件名',
    file_path VARCHAR(512) NOT NULL COMMENT '存储路径（相对路径或完整URL）',
    file_size INT NULL COMMENT '文件大小（字节）',
    file_type VARCHAR(64) NULL COMMENT '文件类型：营业执照/财报/银行流水/其他',
    uploaded_by VARCHAR(64) NULL COMMENT '上传人',
    upload_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    remark VARCHAR(256) NULL,
    INDEX idx_merchant_id (merchant_id),
    FOREIGN KEY (merchant_id) REFERENCES merchant_basic_info(merchant_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
