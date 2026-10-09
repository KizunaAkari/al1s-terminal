package com.al1s.terminal.execution

import android.content.Context
import androidx.room.*

@Entity(tableName="helper_attempts", indices=[Index(value=["packageId"], unique=true), Index(value=["status", "createdAt"])])
data class HelperAttemptEntity(@PrimaryKey val attemptId: String, val packageId: String, val packageHash: String,
    val terminalId: String, val deviceId: String, val permitId: String, val permitExpiresAt: Long, val timeoutSeconds: Int,
    val bodyJson: String, val status: String, val ownerEpoch: String?, val cancellationRequested: Boolean,
    val result: String?, val errorCode: String?, val diagnosticJson: String?, val createdAt: Long,
    val startedAt: Long?, val completedAt: Long?,@ColumnInfo(defaultValue="'formal'") val ownerKind:String="formal")

@Entity(tableName="helper_events", primaryKeys=["attemptId", "sequence"],
    foreignKeys=[ForeignKey(entity=HelperAttemptEntity::class,parentColumns=["attemptId"],childColumns=["attemptId"],onDelete=ForeignKey.RESTRICT)],
    indices=[Index(value=["attemptId", "sequence"], unique=true)])
data class HelperEventEntity(val attemptId: String, val sequence: Long, val eventId: String, val message: String,
    val moduleIndex: Int?, val stepIndex: Int?, val payloadJson: String, val occurredAt: Long)

@Dao interface HelperJournalDao {
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertArtifact(value:HelperArtifactEntity)
    @Query("SELECT * FROM helper_artifacts WHERE attemptId=:id ORDER BY createdAt LIMIT 128")
    fun artifacts(id:String):List<HelperArtifactEntity>
    @Query("SELECT * FROM helper_artifacts WHERE artifactId=:id") fun artifact(id:String):HelperArtifactEntity?
    @Query("UPDATE helper_artifacts SET confirmed=1,remoteArtifactId=:remote WHERE artifactId=:id AND (remoteArtifactId IS NULL OR remoteArtifactId=:remote)")
    fun confirmArtifact(id:String,remote:String):Int
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertAttempt(value: HelperAttemptEntity): Long
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertEvents(values: List<HelperEventEntity>)
    @Query("SELECT * FROM helper_attempts WHERE attemptId=:id") fun attempt(id: String): HelperAttemptEntity?
    @Query("SELECT * FROM helper_attempts WHERE status='running' ORDER BY createdAt LIMIT 1") fun running(): HelperAttemptEntity?
    @Query("SELECT MAX(sequence) FROM helper_events WHERE attemptId=:id") fun latestSequence(id: String): Long?
    @Query("SELECT * FROM helper_events WHERE attemptId=:id AND sequence>:after ORDER BY sequence LIMIT :limit")
    fun events(id: String, after: Long, limit: Int): List<HelperEventEntity>
    @Query("UPDATE helper_attempts SET status='running',ownerEpoch=:epoch,startedAt=:now WHERE attemptId=:id AND status='accepted' AND startedAt IS NULL AND cancellationRequested=0")
    fun markRunning(id: String, epoch: String, now: Long): Int
    @Query("UPDATE helper_attempts SET cancellationRequested=1 WHERE attemptId=:id AND status IN ('accepted','running')")
    fun requestCancellation(id: String): Int
    @Query("UPDATE helper_attempts SET status='completed',result='cancelled',errorCode='execution_cancelled',cancellationRequested=1,completedAt=:now WHERE attemptId=:id AND status='accepted'")
    fun cancelBeforeStart(id: String, now: Long): Int
    @Query("UPDATE helper_attempts SET status='completed',result='failure',errorCode='offline_permit_expired_unstarted',completedAt=:now WHERE attemptId=:id AND status='accepted' AND startedAt IS NULL AND permitExpiresAt<=:now")
    fun settleExpiredUnstarted(id:String,now:Long):Int
    @Query("UPDATE helper_attempts SET status='completed',result=:result,errorCode=:code,diagnosticJson=:diagnostic,completedAt=:now WHERE attemptId=:id AND status='running'")
    fun complete(id: String, result: String, code: String?, diagnostic: String?, now: Long): Int
    @Query("UPDATE helper_attempts SET status='interrupted',result='failure',errorCode='execution_interrupted',completedAt=:now WHERE status='running' AND ownerEpoch!=:epoch")
    fun settleInterrupted(epoch: String, now: Long): Int
}

@Database(entities=[HelperAttemptEntity::class,HelperEventEntity::class,HelperArtifactEntity::class], version=3, exportSchema=true)
abstract class HelperJournal : RoomDatabase() {
    abstract fun dao(): HelperJournalDao
    companion object {
        fun open(context: Context, file: java.io.File): HelperJournal = Room.databaseBuilder(context,
            HelperJournal::class.java, file.absolutePath).addMigrations(object:androidx.room.migration.Migration(1,2) {
                override fun migrate(db:androidx.sqlite.db.SupportSQLiteDatabase) {
                    db.execSQL("CREATE TABLE IF NOT EXISTS helper_artifacts (artifactId TEXT NOT NULL, attemptId TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mediaType TEXT NOT NULL, confirmed INTEGER NOT NULL, createdAt INTEGER NOT NULL, remoteArtifactId TEXT, PRIMARY KEY(artifactId), FOREIGN KEY(attemptId) REFERENCES helper_attempts(attemptId) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                    db.execSQL("CREATE INDEX IF NOT EXISTS index_helper_artifacts_attemptId_confirmed ON helper_artifacts(attemptId,confirmed)")
                }
            },object:androidx.room.migration.Migration(2,3) {
                override fun migrate(db:androidx.sqlite.db.SupportSQLiteDatabase) {
                    db.execSQL("ALTER TABLE helper_attempts ADD COLUMN ownerKind TEXT NOT NULL DEFAULT 'formal'")
                }
            }).build()
    }
}
