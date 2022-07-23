import cv2
import argparse

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image_path", default="/home/bs/Yolov5_StrongSORT_OSNet/dataset/CSU_road/image_a5.jpg", help="图片路径.")
    args = parser.parse_args()
    return args

 
def on_EVENT_LBUTTONDOWN(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        xy = "%d,%d" % (x, y)
        print(x,y)
        cv2.circle(img, (x, y), 2, (0, 0, 255))
        cv2.putText(img, xy, (x, y), cv2.FONT_HERSHEY_PLAIN,1.0, (0,0,255))
        cv2.imshow("image", img)
        
if __name__ == '__main__':
    args = parse_args()
    img=cv2.imread(args.image_path)
    cv2.namedWindow("image")
    cv2.setMouseCallback("image", on_EVENT_LBUTTONDOWN)
    while(1):
        cv2.imshow("image", img)
        key = cv2.waitKey(5) & 0xFF
        if key == ord('q'):
            break
    cv2.destroyAllWindows()
