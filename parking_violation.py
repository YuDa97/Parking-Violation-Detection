import argparse

import os
# limit the number of cpus used by high performance libraries
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import sys
import numpy as np
from pathlib import Path
import torch
import torch.backends.cudnn as cudnn
from skimage.draw import line
from datetime import datetime
import time
import xlwt
from PIL import Image
FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # yolov5 strongsort root directory
WEIGHTS = ROOT / 'weights'

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
if str(ROOT / 'yolov5') not in sys.path:
    sys.path.append(str(ROOT / 'yolov5'))  # add yolov5 ROOT to PATH
if str(ROOT / 'strong_sort') not in sys.path:
    sys.path.append(str(ROOT / 'strong_sort'))  # add strong_sort ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

import logging
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import VID_FORMATS, LoadImages, LoadStreams
from yolov5.utils.general import (LOGGER, check_img_size, non_max_suppression, scale_coords, check_requirements, cv2,
                                  check_imshow, xyxy2xywh, increment_path, strip_optimizer, colorstr, print_args, check_file)
from yolov5.utils.torch_utils import select_device, time_sync
from yolov5.utils.plots import Annotator, colors, save_one_box
from strong_sort.utils.parser import get_config
from strong_sort.strong_sort import StrongSORT

# remove duplicated stream handler to avoid duplicated logging
logging.getLogger().removeHandler(logging.getLogger().handlers[0])

@torch.no_grad()
def run(
        source='0',
        yolo_weights=WEIGHTS / 'yolov5m.pt',  # model.pt path(s),
        strong_sort_weights=WEIGHTS / 'osnet_x0_25_msmt17.pth',  # model.pt path,
        config_strongsort=ROOT / 'strong_sort/configs/strong_sort.yaml',
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        show_vid=True,  # show results
        save_txt=False,  # save results to *.txt
        save_conf=False,  # save confidences in --save-txt labels
        save_crop=False,  # save cropped prediction boxes
        save_vid=False,  # save confidences in --save-txt labels
        nosave=False,  # do not save images/videos
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'runs/track',  # save results to project/name
        name='exp',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=3,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        hide_class=False,  # hide IDs
        half=False,  # use FP16 half-precision inference
        dnn=False,  # use OpenCV DNN for ONNX inference
):

    source = str(source)
    save_img = not nosave and not source.endswith('.txt')  # save inference images
    is_file = Path(source).suffix[1:] in (VID_FORMATS) # 判断输入文件类型是否符合要求 (True)
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)  # download

    # Directories
    if not isinstance(yolo_weights, list):  # single yolo model
        exp_name = yolo_weights.stem
    elif type(yolo_weights) is list and len(yolo_weights) == 1:  # single models after --yolo_weights
        exp_name = Path(yolo_weights[0]).stem
    else:  # multiple models after --yolo_weights
        exp_name = 'ensemble'
    exp_name = name if name else exp_name + "_" + strong_sort_weights.stem
    save_dir = increment_path(Path(project) / exp_name, exist_ok=exist_ok)  # increment run
    (save_dir / 'tracks' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    # Load model
    device = select_device(device)
    model = DetectMultiBackend(yolo_weights, device=device, dnn=dnn, data=None, fp16=half) # 加载yolov5模型
    stride, names, pt = model.stride, model.names, model.pt # 步长(32)、类别名字（00：‘persion', 01'bicycle', 02:'car')、pytorch(true)
    imgsz = check_img_size(imgsz, s=stride)  # check image size （如果不能被32整除要处理成能被32整除）

    # Dataloader
    if webcam:
        show_vid = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = len(dataset)
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = 1
    vid_path, vid_writer, txt_path = [None] * nr_sources, [None] * nr_sources, [None] * nr_sources
    
    # 打开excel工作薄
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Sheet1")

    # 选择格式
    style = xlwt.easyxf('font: bold 1')
    row_num = 0

    FPS =  getattr(dataset, 'fps') # 获取检测视频帧率
    parking_co = [] # 违停区域坐标系
    frame_dic= {} # 与违停区域相交的目标日志
    cache_dic = {} # 违停区域内静止目标日志
    viol_dic = {} # 违停日志
    blanked = np.zeros((getattr(dataset, 'frame_height'), getattr(dataset, 'frame_width'), 3), dtype=np.uint8)  # 新建一个与视频尺寸相同的空白区域
    pts = np.array(([687, 648], [610, 738], [322, 666], [432, 638])) # 感兴趣区域四个顶点坐标

    mask = cv2.fillPoly(blanked, np.int32([pts]), (0, 0, 255)) # 对空白区域进行内部填充 BGR 
   
    # 取出填充像素坐标
    
    x_cord = np.where(mask == 255)[1] 
    y_cord = np.where(mask == 255)[0]

    for q in range(0, len(x_cord)):
        parking_co.append((x_cord[q], y_cord[q])) # 加入到停车区域坐标系中
    # initialize StrongSORT
    cfg = get_config()
    cfg.merge_from_file(opt.config_strongsort)

    # Create as many strong sort instances as there are video sources 加载StrongSort模型
    strongsort_list = []
    for i in range(nr_sources):
        strongsort_list.append(
            StrongSORT(
                strong_sort_weights,
                device,
                max_dist=cfg.STRONGSORT.MAX_DIST,
                max_iou_distance=cfg.STRONGSORT.MAX_IOU_DISTANCE,
                max_age=cfg.STRONGSORT.MAX_AGE,
                n_init=cfg.STRONGSORT.N_INIT,
                nn_budget=cfg.STRONGSORT.NN_BUDGET,
                mc_lambda=cfg.STRONGSORT.MC_LAMBDA,
                ema_alpha=cfg.STRONGSORT.EMA_ALPHA,

            )
        )
    outputs = [None] * nr_sources

    # Run tracking
    # 使用Yolov5进行追踪
    model.warmup(imgsz=(1 if pt else nr_sources, 3, *imgsz))  # warmup
    dt, seen = [0.0, 0.0, 0.0, 0.0], 0
    curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources
    for frame_idx, (path, im, im0s, vid_cap, s) in enumerate(dataset): # im 为图像数据，如果输入是视频则shape(Batch, color, height, weight)
        t1 = time_sync()
        im = torch.from_numpy(im).to(device)
        im = im.half() if half else im.float()  # uint8 to fp16/32
        im /= 255.0  # 0 - 255 to 0.0 - 1.0
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        t2 = time_sync()
        dt[0] += t2 - t1

        # Inference
        visualize = increment_path(save_dir / Path(path[0]).stem, mkdir=True) if visualize else False
        pred = model(im, augment=augment, visualize=visualize) # （属于每个类别的概率)
        t3 = time_sync()
        dt[1] += t3 - t2

        # Apply NMS (非极大值抑制)
        pred = non_max_suppression(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
        dt[2] += time_sync() - t3

        # Process detections
        for i, det in enumerate(pred):  # detections per image
            seen += 1
            if webcam:  # nr_sources >= 1
                p, im0, _ = path[i], im0s[i].copy(), dataset.count
                p = Path(p)  # to Path
                s += f'{i}: '
                txt_file_name = p.name
                save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
            else:
                p, im0, _ = path, im0s.copy(), getattr(dataset, 'frame', 0)
                p = Path(p)  # to Path
                # video file
                if source.endswith(VID_FORMATS):
                    txt_file_name = p.stem
                    save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
                # folder with imgs
                else:
                    txt_file_name = p.parent.name  # get folder name containing current img
                    save_path = str(save_dir / p.parent.name)  # im.jpg, vid.mp4, ...
            curr_frames[i] = im0

            txt_path = str(save_dir / 'tracks' / txt_file_name)  # im.txt
            s += '%gx%g ' % im.shape[2:]  # print string
            imc = im0.copy() if save_crop else im0  # for save_crop
            image = Image.fromarray(imc)
            annotator = Annotator(im0, line_width=2, pil=not ascii, mask=mask)
            if cfg.STRONGSORT.ECC:  # camera motion compensation
                strongsort_list[i].tracker.camera_update(prev_frames[i], curr_frames[i])

            if det is not None and len(det):
                # Rescale boxes from img_size to im0 size
                det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string

                xywhs = xyxy2xywh(det[:, 0:4]) #中心宽高
                confs = det[:, 4] # 置信度
                clss = det[:, 5] # 类别

                # pass detections to strongsort
                t4 = time_sync()
                outputs[i] = strongsort_list[i].update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                t5 = time_sync()
                dt[3] += t5 - t4

                # 画出框线并且判断是否有车辆进入违停区域
                if len(outputs[i]) > 0: 
                    for j, (output, conf) in enumerate(zip(outputs[i], confs)): # 处理每帧图像中的每个检测目标
    
                        bboxes = output[0:4] # 左上角点和右下角点（xyxy)
                        id = output[4] # 追踪ID
                        cls = output[5] # 类别 coco数据集（0：‘persion', 1'bicycle', 2:'car'）

                        if frame_idx %  FPS == 0: # 每秒检测一次
                            if len(frame_dic)>10^7: # 防止溢出
                                frame_dic={}
                            for out in outputs[i]:
                                bbox = out[0:4]
                                frame_idx_s, cls_s, id_s = str(frame_idx), names[int(out[5])], str(int(out[4]))
                                # 获取左右框线：
                                #bbox_left_line_co = list(zip(*line(*(int(bboxes[0]),int(bboxes[1])), *(int(bboxes[0]),int(bboxes[3]))))) # 左框线
                                #bbox_right_line_co = list(zip(*line(*(int(bboxes[2]),int(bboxes[1])), *(int(bboxes[2]),int(bboxes[3]))))) # 右框线
                                # 获取下框线：
                                bbox_bottom_line_co = list(zip(*line(*(int(bbox[0])+25,int(bbox[3])), *(int(bbox[2])-25,int(bbox[3]))))) 
                                #violate_start = time.time()
                                if len(intersection(bbox_bottom_line_co, parking_co))>0:
                                #if intersection(bbox_bottom_line_co, parking_co):
                                    cur_frame_key = frame_idx_s + cls_s + id_s
                                    pre_frame_key = str(int(frame_idx-FPS)) +cls_s + id_s 
                                    frame_dic[cur_frame_key] = bbox

                                    previous_bbox_co = frame_dic.get(pre_frame_key, []) # 获取当前目标前一秒检测框,若无返回空列表
                                    if len(previous_bbox_co): 
                                        if immobile(bbox, previous_bbox_co) == True: # 若目标静止
                                            if not cache_dic.get(cls_s+id_s, 0): # 如果静止目标没在cache_dic中

                                                t_start = datetime.now() # 设定计时器
                                                cache_dic[cls_s+id_s] = str(t_start)
                                            # 若目标在cache_dic但不在viol_dic中
                                            if cache_dic.get(cls_s+id_s, 0) and not viol_dic.get(cls_s+id_s, 0):

                                                t_start_cm = cache_dic[cls_s+id_s][0:19] # 舍掉毫秒
                                                t_spending = (datetime.now() - datetime.strptime(t_start_cm,
                                                                                                '%Y-%m-%d %H:%M:%S')).total_seconds()

                                                print(f'{cls_s}{id_s} 于{t_start_cm} 停留 {t_spending:.2f} 秒')
                                                if t_spending > 5: # 若停留时间大于...秒，违停车辆写入EXCEL以及viol_dic,并且截图
                                                    sheet.write(row_num, 0, str(t_start_cm), style)
                                                    # sheet.write(row_num, 1, str(round(t_spending, 2)), style)
                                                    sheet.write(row_num, 1, cls_s + id_s, style)
                                                    row_num += 1
                                                    workbook.save(Path(save_dir / 'details.xls'))

                                                    viol_dic[cls_s+id_s] = t_spending
                                                    # print(t_start_cm, t_spending, datetime.now())
                                                    cropped = image.crop((int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))) 
                                                    cropped.save(Path(save_dir / f'{cls_s}{id_s}.jpg'))

                            #violate_end = time.time()
                            #violate_time = violate_end - violate_start
                            #print(f'违停检测用时: {violate_time}')
                        if save_txt:
                            # to MOT format
                            bbox_left = output[0]
                            bbox_top = output[1]
                            bbox_w = output[2] - output[0]
                            bbox_h = output[3] - output[1]
                            # Write MOT compliant results to file
                            with open(txt_path + '.txt', 'a') as f:
                                f.write(('%g ' * 10 + '\n') % (frame_idx + 1, id, bbox_left,  # MOT format
                                                               bbox_top, bbox_w, bbox_h, -1, -1, -1, i))

                        if save_vid or save_crop or show_vid:  # Add bbox to image
                            c = int(cls)  # integer class
                            id = int(id)  # integer id
                            label = None if hide_labels else (f'{id} {names[c]}' if hide_conf else \
                                (f'{id} {conf:.2f}' if hide_class else f'{id} {names[c]} {conf:.2f}'))
                            annotator.box_label(bboxes, label, color=colors(c, True))
                            if len(viol_dic):
                                text = ''
                                for key, time in viol_dic.items():
                                    text += key + 'illegal parking for more than' + f'{time:.2f}' + 's!' 
                                annotator.add_alarm(xy=(15,19), label=text)
                            #cv2.fillPoly(imc, np.int32([pts]), (0,0,255)) # 添加上违停区域
                            if save_crop:
                                txt_file_name = txt_file_name if (isinstance(path, list) and len(path) > 1) else ''
                                save_one_box(bboxes, imc, file=save_dir / 'crops' / txt_file_name / names[c] / f'{id}' / f'{p.stem}.jpg', BGR=True)

                LOGGER.info(f'{s}Done. YOLO:({t3 - t2:.3f}s), StrongSORT:({t5 - t4:.3f}s)')

            else:
                strongsort_list[i].increment_ages()
                LOGGER.info('No detections')

            # Stream results
            im0 = annotator.result()
            if show_vid:
                cv2.imshow(str(p), im0)
                cv2.waitKey(1)  # 1 millisecond

            # Save results (image with detections)
            if save_vid:
                if vid_path[i] != save_path:  # new video
                    vid_path[i] = save_path
                    if isinstance(vid_writer[i], cv2.VideoWriter):
                        vid_writer[i].release()  # release previous video writer
                    if vid_cap:  # video
                        fps = vid_cap.get(cv2.CAP_PROP_FPS)
                        w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    else:  # stream
                        fps, w, h = 30, im0.shape[1], im0.shape[0]
                    save_path = str(Path(save_path).with_suffix('.mp4'))  # force *.mp4 suffix on results videos
                    vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                vid_writer[i].write(im0)

            prev_frames[i] = curr_frames[i]

    # Print results
    t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
    LOGGER.info(f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS, %.1fms strong sort update per image at shape {(1, 3, *imgsz)}' % t)
    if save_txt or save_vid:
        s = f"\n{len(list(save_dir.glob('tracks/*.txt')))} tracks saved to {save_dir / 'tracks'}" if save_txt else ''
        LOGGER.info(f"Results saved to {colorstr('bold', save_dir)}{s}")
    if update:
        strip_optimizer(yolo_weights)  # update model (to fix SourceChangeWarning)


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--yolo-weights', nargs='+', type=str, default=WEIGHTS / 'yolov5m.pt', help='model.pt path(s)')
    parser.add_argument('--strong-sort-weights', type=str, default=WEIGHTS / 'osnet_x0_25_msmt17.pth')
    parser.add_argument('--config-strongsort', type=str, default='strong_sort/configs/strong_sort.yaml')
    parser.add_argument('--source', type=str, default='dataset/me/video.mp4', help='file/dir/URL/glob, 0 for webcam')  
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=[640], help='inference size h,w')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='NMS IoU threshold')
    parser.add_argument('--max-det', type=int, default=1000, help='maximum detections per image')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--show-vid', default=True, action='store_true', help='display tracking video results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-crop', action='store_true', help='save cropped prediction boxes')
    parser.add_argument('--save-vid', action='store_true', help='save video tracking results')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    # class 0 is person, 1 is bycicle, 2 is car... 79 is oven
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --classes 0, or --classes 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--visualize', action='store_true', help='visualize features')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default=ROOT / 'runs/track', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--line-thickness', default=3, type=int, help='bounding box thickness (pixels)')
    parser.add_argument('--hide-labels', default=False, action='store_true', help='hide labels')
    parser.add_argument('--hide-conf', default=False, action='store_true', help='hide confidences')
    parser.add_argument('--hide-class', default=False, action='store_true', help='hide IDs')
    parser.add_argument('--half', action='store_true', help='use FP16 half-precision inference')
    parser.add_argument('--dnn', action='store_true', help='use OpenCV DNN for ONNX inference')
    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1  # expand
    print_args(vars(opt))
    return opt


def main(opt):
    check_requirements(requirements=ROOT / 'requirements.txt', exclude=('tensorboard', 'thop'))
    run(**vars(opt))

# 判断是否相交
def intersection(Line, parking_co):

    bottom_cross = list(set(Line) & set(parking_co))
    return bottom_cross
    #return set(parking_co).isdisjoint(set(Line))

# 检测目标是否静止
def immobile(bbox, previous_bbox):
    total = abs(bbox[0] - previous_bbox[0]) + abs(bbox[1] - previous_bbox[1]) + \
        abs(bbox[2] - previous_bbox[2]) + abs(bbox[3] - previous_bbox[3])
    if total <= 50: # Yolo边框存在波动
        return True
    else:
        return False

if __name__ == "__main__":
    opt = parse_opt()
    main(opt)