-- ======================================================================
-- 审批工作流模块（M4）DDL 迁移脚本
-- 包含两张表：approval_order（审批主表）、approval_task（审批任务表）
-- 执行前请确认 merchant_basic_info 表已存在
-- ======================================================================

-- ------------------------------------------------------------------
-- 表1：approval_order（审批主表）
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `approval_order` (
    `id`                    INT            NOT NULL AUTO_INCREMENT       COMMENT '主键',
    `merchant_id`           BIGINT         NOT NULL                      COMMENT '客商ID(外键关联 merchant_basic_info.merchant_id)',
    `applicant`             VARCHAR(64)    NOT NULL                      COMMENT '申请人',
    `apply_time`            DATETIME       DEFAULT NULL                  COMMENT '申请时间',
    `status`                VARCHAR(32)    NOT NULL DEFAULT 'pending_dept' COMMENT 'pending_dept/parallel_signing/final_signing/approved/rejected',
    `current_step`          VARCHAR(32)    DEFAULT NULL                  COMMENT '当前步骤描述',
    `total_score`           DECIMAL(8,4)   DEFAULT NULL                  COMMENT '评分总分（冗余）',
    `rating`                VARCHAR(16)    DEFAULT NULL                  COMMENT '评级（冗余）',
    `dimension_scores_json` TEXT           DEFAULT NULL                  COMMENT '各维度得分JSON',
    `remark`                VARCHAR(512)   DEFAULT NULL                  COMMENT '申请备注',
    `updated_at`            DATETIME       DEFAULT NULL                  COMMENT '更新时间',
    PRIMARY KEY (`id`),
    INDEX `idx_approval_order_merchant_id` (`merchant_id`),
    CONSTRAINT `fk_approval_order_merchant`
        FOREIGN KEY (`merchant_id`) REFERENCES `merchant_basic_info` (`merchant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='审批主表';


-- ------------------------------------------------------------------
-- 表2：approval_task（审批任务表）
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `approval_task` (
    `id`             INT            NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `order_id`       INT            NOT NULL                 COMMENT '审批单ID(外键关联 approval_order.id)',
    `assignee`       VARCHAR(64)    NOT NULL                 COMMENT '处理人',
    `role_type`      VARCHAR(32)    NOT NULL                 COMMENT 'dept_head/market/compliance/finance/executive',
    `action`         VARCHAR(16)    DEFAULT NULL             COMMENT 'approve/reject',
    `comment`        VARCHAR(512)   DEFAULT NULL             COMMENT '处理意见',
    `task_status`    VARCHAR(32)    NOT NULL DEFAULT 'pending' COMMENT 'pending/done/cancelled',
    `task_deadline`  DATETIME       DEFAULT NULL             COMMENT '截止时间',
    `handled_at`     DATETIME       DEFAULT NULL             COMMENT '处理时间',
    `created_at`     DATETIME       DEFAULT NULL             COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_approval_task_order_id` (`order_id`),
    INDEX `idx_approval_task_assignee` (`assignee`),
    CONSTRAINT `fk_approval_task_order`
        FOREIGN KEY (`order_id`) REFERENCES `approval_order` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='审批任务表';
