#include "MaaCallbacks.h"

namespace {
struct Scope {
    JavaVM* vm;
    JNIEnv* env = nullptr;
    bool attached;
    explicit Scope(JavaVM* machine) : vm(machine), attached(false) {
        if (vm->GetEnv(reinterpret_cast<void**>(&env),JNI_VERSION_1_6) == JNI_EDETACHED)
            attached = vm->AttachCurrentThread(&env,nullptr) == JNI_OK;
        if (env) env->PushLocalFrame(16);
    }
    ~Scope() { if (env) { if (env->ExceptionCheck()) env->ExceptionClear(); env->PopLocalFrame(nullptr); }
        if (attached) vm->DetachCurrentThread(); }
};
jintArray rectangle(JNIEnv* env, const MaaRect* box) {
    jint data[] = {box ? box->x : 0,box ? box->y : 0,box ? box->width : 0,box ? box->height : 0};
    auto result = env->NewIntArray(4); if (result) env->SetIntArrayRegion(result,0,4,data); return result;
}
}

std::unique_ptr<MaaCallbacks> MaaCallbacks::registerHandlers(JNIEnv* env, MaaResource* resource, jobject handlers) {
    auto result = std::make_unique<MaaCallbacks>();
    env->GetJavaVM(&result->vm_);
    result->handlers_ = env->NewGlobalRef(handlers);
    auto type = env->GetObjectClass(handlers);
    result->recognition_ = env->GetMethodID(type,"recognize",
        "(JJLjava/lang/String;Ljava/lang/String;Ljava/lang/String;J[I)Lcom/al1s/terminal/maa/NativeRecognitionResult;");
    result->action_ = env->GetMethodID(type,"act","(JJLjava/lang/String;Ljava/lang/String;Ljava/lang/String;J[I)Z");
    auto action_names = env->GetMethodID(type,"getActionNames","()[Ljava/lang/String;");
    auto recognition_names = env->GetMethodID(type,"getRecognitionNames","()[Ljava/lang/String;");
    if (env->ExceptionCheck() || !result->handlers_) { env->DeleteLocalRef(type); return nullptr; }
    auto actions = static_cast<jobjectArray>(env->CallObjectMethod(handlers,action_names));
    auto recognitions = static_cast<jobjectArray>(env->CallObjectMethod(handlers,recognition_names));
    auto register_action = maaApi<decltype(&MaaResourceRegisterCustomAction)>("MaaResourceRegisterCustomAction");
    auto register_recognition = maaApi<decltype(&MaaResourceRegisterCustomRecognition)>("MaaResourceRegisterCustomRecognition");
    bool ok = register_action && register_recognition && actions && recognitions && !env->ExceptionCheck();
    for (int i = 0; ok && i < env->GetArrayLength(actions); ++i) {
        auto name = static_cast<jstring>(env->GetObjectArrayElement(actions,i));
        ok = register_action(resource,maaText(env,name).c_str(),action,result.get()); env->DeleteLocalRef(name);
    }
    for (int i = 0; ok && i < env->GetArrayLength(recognitions); ++i) {
        auto name = static_cast<jstring>(env->GetObjectArrayElement(recognitions,i));
        ok = register_recognition(resource,maaText(env,name).c_str(),recognize,result.get()); env->DeleteLocalRef(name);
    }
    env->DeleteLocalRef(type);
    if (actions) env->DeleteLocalRef(actions);
    if (recognitions) env->DeleteLocalRef(recognitions);
    if (!ok) { maaError(env,"Maa handler registration failed"); return nullptr; }
    return result;
}

MaaCallbacks::~MaaCallbacks() { Scope scope(vm_); if (scope.env && handlers_) scope.env->DeleteGlobalRef(handlers_); }

MaaBool MaaCallbacks::recognize(MaaContext* context, MaaTaskId id, const char* node, const char* name, const char* parameters,
    const MaaImageBuffer* image, const MaaRect* roi, void* argument, MaaRect* output, MaaStringBuffer* detail) {
    auto owner = static_cast<MaaCallbacks*>(argument);
    Scope scope(owner->vm_); auto env = scope.env; if (!env) return false;
    auto result = env->CallObjectMethod(owner->handlers_,owner->recognition_,reinterpret_cast<jlong>(context),id,
        env->NewStringUTF(node),env->NewStringUTF(name),env->NewStringUTF(parameters),reinterpret_cast<jlong>(image),rectangle(env,roi));
    if (!result || env->ExceptionCheck()) return false;
    auto type = env->GetObjectClass(result);
    if (!env->GetBooleanField(result,env->GetFieldID(type,"hit","Z"))) return false;
    auto array = static_cast<jintArray>(env->GetObjectField(result,env->GetFieldID(type,"box","[I")));
    auto body = static_cast<jstring>(env->GetObjectField(result,env->GetFieldID(type,"detail","Ljava/lang/String;")));
    if (!array || env->GetArrayLength(array) != 4 || !body) return false;
    jint data[4]; env->GetIntArrayRegion(array,0,4,data);
    *output = MaaRect {data[0],data[1],data[2],data[3]};
    return maaApi<decltype(&MaaStringBufferSet)>("MaaStringBufferSet")(detail,maaText(env,body).c_str());
}

MaaBool MaaCallbacks::action(MaaContext* context, MaaTaskId id, const char* node, const char* name, const char* parameters,
    MaaRecoId reco, const MaaRect* box, void* argument) {
    auto owner = static_cast<MaaCallbacks*>(argument);
    Scope scope(owner->vm_); auto env = scope.env; if (!env) return false;
    auto result = env->CallBooleanMethod(owner->handlers_,owner->action_,reinterpret_cast<jlong>(context),id,
        env->NewStringUTF(node),env->NewStringUTF(name),env->NewStringUTF(parameters),reco,rectangle(env,box));
    return !env->ExceptionCheck() && result;
}
