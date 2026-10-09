#pragma once
#include "NativeApi.h"
#include <memory>

class MaaCallbacks {
public:
    static std::unique_ptr<MaaCallbacks> registerHandlers(JNIEnv* env, MaaResource* resource, jobject handlers);
    ~MaaCallbacks();
private:
    JavaVM* vm_ = nullptr;
    jobject handlers_ = nullptr;
    jmethodID recognition_ = nullptr;
    jmethodID action_ = nullptr;
    static MaaBool recognize(MaaContext*, MaaTaskId, const char*, const char*, const char*, const MaaImageBuffer*,
        const MaaRect*, void*, MaaRect*, MaaStringBuffer*);
    static MaaBool action(MaaContext*, MaaTaskId, const char*, const char*, const char*, MaaRecoId, const MaaRect*, void*);
};
