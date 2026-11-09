import assert from "node:assert/strict";
import test from "node:test";
import {
  canContinueFromPreferences,
  canContinueFromRoute,
  nextPlannerStep,
  previousPlannerStep,
} from "./plannerFlow.ts";

test("planner advances and returns one screen at a time", () => {
  assert.equal(nextPlannerStep("home"), "route");
  assert.equal(nextPlannerStep("route"), "preferences");
  assert.equal(nextPlannerStep("preferences"), "result");
  assert.equal(nextPlannerStep("result"), "result");
  assert.equal(previousPlannerStep("result"), "preferences");
  assert.equal(previousPlannerStep("preferences"), "route");
  assert.equal(previousPlannerStep("route"), "home");
  assert.equal(previousPlannerStep("home"), "home");
});

test("route step requires distinct stops and a completed timetable lookup", () => {
  assert.equal(canContinueFromRoute("", "YEOUIDO", "ready"), false);
  assert.equal(canContinueFromRoute("MAGOK", "", "ready"), false);
  assert.equal(canContinueFromRoute("MAGOK", "MAGOK", "ready"), false);
  assert.equal(canContinueFromRoute("MAGOK", "YEOUIDO", "loading"), false);
  assert.equal(canContinueFromRoute("MAGOK", "YEOUIDO", "error"), false);
  assert.equal(canContinueFromRoute("MAGOK", "YEOUIDO", "ready"), true);
});

test("preferences step requires a selected sailing", () => {
  assert.equal(canContinueFromPreferences(""), false);
  assert.equal(canContinueFromPreferences("MAGOK-YEOUIDO-16:00"), true);
});
