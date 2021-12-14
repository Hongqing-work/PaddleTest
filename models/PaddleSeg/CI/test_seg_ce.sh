#!/bin/bash

python -m pip install pip==20.2.4
echo -e '*****************paddle_version*****'
    python -c 'import paddle;print(paddle.version.commit)'
echo -e '*****************detection_version****'
    git rev-parse HEAD

err_sign=false
if [ -d "log" ];then rm -rf log
fi
mkdir log
if [ -d "log_err" ];then rm -rf log_err
fi
mkdir log_err
if [ -d "output" ];then rm -rf output
fi
# prepare dynamic data
mkdir data
if [ -d "data/cityscapes" ];then rm -rf data/cityscapes
fi
ln -s ${data_path}/cityscape data/cityscapes
if [ -d "data/VOCdevkit" ]; then rm -rf data/VOCdevkit
fi
ln -s ${data_path}/pascalvoc/VOCdevkit data/VOCdevkit
if [ -d "seg_dynamic_pretrain" ];then rm -rf seg_dynamic_pretrain
fi
ln -s ${data_path}/seg_dynamic_pretrain seg_dynamic_pretrain

print_result(){
    if [ $? -ne 0 ];then
        echo -e "${model},${mode},FAIL"
        cd ${log_dir}/log_err
        if [ ! -d ${model} ];then
            mkdir ${model}
        fi
        cd ../${model_type_path}
        mv ${log_dir}/log/${model}/${model}_${mode}.log ${log_dir}/log_err/${model}/
        err_sign=true
        cat ${log_dir}/log_err/${model}/${model}_${mode}.log
        #exit 1
    else
        echo -e "${model},${mode},SUCCESS"
    fi
}

# run dynamic models
python -m pip install --upgrade pip
pip install -r requirements.txt
log_dir=.
model_type_path=
if [ "$1" == 'develop_d1' ];then
find . | grep configs | grep .yml | grep -v _base_ | grep -v quick_start | grep -v EISeg | grep -v setr | grep -v portraitnet | grep -v contrib | grep -v segformer | grep -v deeplabv3 | tee dynamic_config_all
elif [ "$1" == 'develop_d2' ];then
find . | grep configs | grep .yml | grep -v _base_ | grep -v quick_start | grep -v setr | grep segformer | grep -v contrib | grep -v EISeg | tee dynamic_config_segformer
find . | grep configs | grep .yml | grep -v _base_ | grep -v quick_start | grep -v setr | grep deeplabv3 | grep -v contrib | grep -v EISeg | tee dynamic_config_deeplabv3
cat dynamic_config_segformer dynamic_config_deeplabv3 >>dynamic_config_all
else
find . | grep configs | grep .yml | grep -v _base_ | grep -v quick_start | grep -v EISeg | grep -v setr | grep -v portraitnet | grep -v contrib | grep -v segformer | tee dynamic_config_all
fi
sed -i "s/trainaug/train/g" configs/_base_/pascal_voc12aug.yml
skip_export_model='espnet_cityscapes_1024x512_120k gscnn_resnet50_os8_cityscapes_1024x512_80k pointrend_resnet101_os8_cityscapes_1024×512_80k pointrend_resnet101_os8_voc12aug_512×512_40k pointrend_resnet50_os8_cityscapes_1024×512_80k pointrend_resnet50_os8_voc12aug_512×512_40k'
# dynamic fun
TRAIN_MUlTI_DYNAMIC(){
    export CUDA_VISIBLE_DEVICES=$cudaid2
    mode=train_multi_dynamic
    if [[ ${model} =~ 'segformer' ]];then
        echo -e "${model} does not test multi_train！"
    else
        python -m paddle.distributed.launch train.py \
           --config ${config} \
           --save_interval 100 \
           --iters 10 \
           --num_workers 8 \
           --save_dir output/${model} >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
        print_result
    fi
}
TRAIN_SINGLE_DYNAMIC(){
    export CUDA_VISIBLE_DEVICES=$cudaid1
    mode=train_single_dynamic
    if [[ ${model} =~ 'segformer' ]];then
        echo -e "${model} does not test single_train！"
    else
        python train.py \
           --config ${config} \
           --save_interval 100 \
           --iters 10 \
           --num_workers 8 \
           --save_dir output/${model} >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
        print_result
    fi
}
TRAIN_SINGLE_DYNAMIC_BS1(){
    export CUDA_VISIBLE_DEVICES=$cudaid1
    mode=train_single_dynamic_bs1
    if [[ ${model} =~ 'segformer' ]];then
        echo -e "${model} does not test single_dynamic_bs1_train！"
    else
        python train.py \
           --config ${config} \
           --save_interval 100 \
           --iters 10 \
           --batch_size=1 \
           --num_workers 8 \
           --save_dir output/${model} >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
        print_result
    fi
}
EVAL_DYNAMIC(){
    export CUDA_VISIBLE_DEVICES=$cudaid2
    mode=eval_dynamic
    python -m paddle.distributed.launch val.py \
       --config ${config} \
       --model_path seg_dynamic_pretrain/${model}/model.pdparams >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
    print_result
}
PREDICT_DYNAMIC(){
    mode=predict_dynamic
    python predict.py \
       --config ${config} \
       --model_path seg_dynamic_pretrain/${model}/model.pdparams \
       --image_path demo/${predict_pic} \
       --save_dir output/${model}/result >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
    print_result
}
EXPORT_DYNAMIC(){
    mode=export_dynamic
    if [[ -z `echo ${skip_export_model} | grep -w ${model}` ]];then
        export CUDA_VISIBLE_DEVICES=$cudaid1
        python export.py \
           --config ${config} \
           --model_path seg_dynamic_pretrain/${model}/model.pdparams \
           --save_dir ./inference_model/${model} >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
        print_result
    else
        echo -e "${model} does not support export!"
    fi
}
PYTHON_INFER_DYNAMIC(){
    mode=python_infer_dynamic
    if [[ ${model} =~ 'dnlnet' || ${model} =~ 'gscnn' || ${model} =~ 'pointrend' || ${model} =~ 'espnet' ]];then
        echo -e "${model} does not test python_infer！"
    else
        export PYTHONPATH=`pwd`
        python deploy/python/infer.py \
           --config ./inference_model/${model}/deploy.yaml \
           --image_path ./demo/${predict_pic} \
           --save_dir ./python_infer_output/${model} >${log_dir}/log/${model}/${model}_${mode}.log 2>&1
        print_result
    fi
}
for config in `cat dynamic_config_all`
do
tmp=${config##*/}
model=${tmp%.*}
echo "${model}"
cd log && mkdir ${model}
cd ..
predict_pic='leverkusen_000029_000019_leftImg8bit.png'
if [[ -n `echo ${model} | grep voc12` ]];then
    predict_pic='2007_000033.jpg'
fi
if [[ -n `echo ${model} | grep voc12` ]] && [[ ! -f seg_dynamic_pretrain/${model}/model.pdparams ]];then
    wget -P seg_dynamic_pretrain/${model}/ https://bj.bcebos.com/paddleseg/dygraph/pascal_voc12/${model}/model.pdparams
    if [ ! -s seg_dynamic_pretrain/${model}/model.pdparams ];then
        echo "${model} url is bad!"
    else
        TRAIN_MUlTI_DYNAMIC
        TRAIN_SINGLE_DYNAMIC_BS1
        TRAIN_SINGLE_DYNAMIC
        EVAL_DYNAMIC
        PREDICT_DYNAMIC
        EXPORT_DYNAMIC
        PYTHON_INFER_DYNAMIC
    fi
elif [[ -z `echo ${model} | grep voc12` ]] && [[ ! -f seg_dynamic_pretrain/${model}/model.pdparams ]];then
    wget -P seg_dynamic_pretrain/${model}/ https://paddleseg.bj.bcebos.com/dygraph/cityscapes/${model}/model.pdparams
    if [ ! -s seg_dynamic_pretrain/${model}/model.pdparams ];then
        echo "${model} url is bad!"
    else
        TRAIN_MUlTI_DYNAMIC
        TRAIN_SINGLE_DYNAMIC_BS1
        TRAIN_SINGLE_DYNAMIC
        EVAL_DYNAMIC
        PREDICT_DYNAMIC
        EXPORT_DYNAMIC
        PYTHON_INFER_DYNAMIC
    fi
else
    TRAIN_MUlTI_DYNAMIC
    TRAIN_SINGLE_DYNAMIC_BS1
    TRAIN_SINGLE_DYNAMIC
    EVAL_DYNAMIC
    PREDICT_DYNAMIC
    EXPORT_DYNAMIC
    PYTHON_INFER_DYNAMIC
fi
done



if [ "${err_sign}" = true ];then
    exit 1
fi
