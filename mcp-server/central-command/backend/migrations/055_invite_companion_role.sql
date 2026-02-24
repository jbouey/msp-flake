-- Migration: 055_invite_companion_role.sql
-- Description: Allow 'companion' role in admin_user_invites CHECK constraint
-- Fix: 053 added companion to admin_users but missed admin_user_invites
-- Created: 2026-02-24

ALTER TABLE admin_user_invites DROP CONSTRAINT IF EXISTS admin_user_invites_role_check;
ALTER TABLE admin_user_invites ADD CONSTRAINT admin_user_invites_role_check
    CHECK (role IN ('admin', 'operator', 'readonly', 'companion'));
