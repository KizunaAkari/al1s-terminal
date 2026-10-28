package com.al1s.terminal.runtime

object AttemptReconciliation {
    enum class Action { WAIT, OBSERVE, CANCEL, START, REPORT, SETTLE_UNSTARTED }
    fun decide(helperStatus: String?, hashMatches: Boolean, cancelled: Boolean, permitValid: Boolean,
        epochMatches:Boolean=true,started:Boolean=false): Action {
        if (!hashMatches || !epochMatches || helperStatus == null) return Action.WAIT
        return when (helperStatus) {
            "completed", "interrupted" -> Action.REPORT
            "running" -> if (cancelled) Action.CANCEL else Action.OBSERVE
            "accepted" -> if(started)Action.WAIT else if (cancelled) Action.CANCEL else if (permitValid) Action.START else Action.SETTLE_UNSTARTED
            else -> Action.WAIT
        }
    }
}
