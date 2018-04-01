import os, sys
import torch
import torch.nn as nn
from torch.autograd import Variable

from text2pal_newCA2.model import *
from text2pal_newCA2.utils import *

SOS_token = 0


class TrainGAN(object):
    def __init__(self, train_loader, val_loader, encoder, decoder, discriminator, args):
        self.args = args
        self.train_loader = train_loader
        self.val_loader = val_loader
        # Generator
        self.encoder = encoder
        self.encoder.apply(init_weights_normal)
        self.decoder = decoder
        self.decoder.apply(init_weights_normal)
        self.G_parameters = list(encoder.parameters()) + list(decoder.parameters())
        # Discriminator
        self.D = discriminator
        self.D.apply(init_weights_normal)
        # Loss Function and Optimizer
        self.criterion_GAN = nn.BCELoss()
        self.criterion_smoothL1 = nn.SmoothL1Loss()
        self.optimizer_G = torch.optim.Adam(self.G_parameters,
                                            lr=args.lr, weight_decay=args.weight_decay)
        self.optimizer_D = torch.optim.Adam(self.D.parameters(),
                                            lr=args.lr, betas=(args.beta1, args.beta2))

    def train(self):

        for epoch in range(1, self.args.epochs + 1):
            steps = 0

            for i, data in enumerate(self.train_loader):

                # Prepare training data.
                txt_embeddings, real_palettes = data
                batch_size = txt_embeddings.size(0)

                indices = torch.nonzero(txt_embeddings)
                temp = []
                for index in indices:
                    temp.append(index[0])

                each_input_size = [0]*batch_size
                for j in range(batch_size):
                    each_input_size[j] = temp.count(j)

                txt_embeddings = Variable(txt_embeddings).cuda()
                real_palettes = Variable(real_palettes).cuda()
                real_palettes = real_palettes.float()

                # Create the labels which are later used as input for the BCE loss.
                batch_size = real_palettes.size(0)
                real_labels = Variable(torch.ones(batch_size)).cuda()
                fake_labels = Variable(torch.zeros(batch_size)).cuda()

                # Prepare input and output variables.
                decoder_context = Variable(torch.zeros(1, batch_size, self.decoder.hidden_size)).cuda()
                palette = Variable(torch.FloatTensor([[SOS_token]])).unsqueeze(2).expand(1, batch_size, 3).cuda()
                fake_palettes = Variable(torch.FloatTensor(batch_size, 15).zero_()).cuda()

                # ================== Generate 5 color palette ==================#
                # Encoder
                encoder_hidden = self.encoder.init_hidden(batch_size)
                encoder_outputs, decoder_hidden, mu, logvar = self.encoder(txt_embeddings, encoder_hidden)
                
                signal = 0
                # Decoder
                for i in range(5):
                    palette, decoder_context, decoder_hidden, _ = self.decoder(palette, decoder_context,
                                                                               decoder_hidden, encoder_outputs,
                                                                               each_input_size, signal)
                    fake_palettes[:, 3 * i:3 * (i + 1)] = palette
                    palette = palette.unsqueeze(0)

                each_input_size_ = Variable(torch.FloatTensor(each_input_size).unsqueeze(1)
                                            .expand(batch_size,self.decoder.hidden_size), # FC encoder = 300, RNN encoder = 150
                                            requires_grad=False).cuda() # B x 150

                encoder_outputs = torch.sum(encoder_outputs, 0)
                encoder_outputs = torch.div(encoder_outputs, each_input_size_)

                # ================== Train the discriminator ==================#
                # Compute BCE Loss using real palettes.
                real = self.D(real_palettes, encoder_outputs)
                loss_D_real = self.criterion_GAN(real, real_labels)

                # Compute BCE Loss using fake palettes.
                fake = self.D(fake_palettes, encoder_outputs)
                loss_D_fake = self.criterion_GAN(fake, fake_labels)

                # Backprop + Optimize.
                loss_D = loss_D_real + loss_D_fake
                self.optimizer_D.zero_grad()
                loss_D.backward(retain_graph=True)
                self.optimizer_D.step()

                # ==================== Train the generator ====================#
                # Compute BCE Loss (fake the discriminator).
                loss_G_GAN = self.criterion_GAN(fake, real_labels)

                # Compute L1 Loss.
                loss_G_smoothL1 = self.criterion_smoothL1(fake_palettes, real_palettes) * self.args.lambda_sL1

                # Compute KL loss.
                kl_loss = KL_loss(mu, logvar) * self.args.lambda_KL

                # Backprop + Optimize.
                loss_G = loss_G_GAN + loss_G_smoothL1 + kl_loss
                self.optimizer_G.zero_grad()
                loss_G.backward()
                self.optimizer_G.step()

                steps += 1
                if steps % self.args.log_interval == 0:
                    sys.stdout.write(
                        '\rEpoch [{}], Batch[{}] - d_loss: {:.6f}, g_loss: {:.6f}'.format(
                            epoch, steps, loss_D.data[0], loss_G.data[0]))

            if epoch % 10 == 0:
                save_dir = os.path.join(self.args.save_dir, self.args.loss_combination,
                                        'sL1'+str(self.args.lambda_sL1)+'_KL'+str(self.args.lambda_KL))
                if not os.path.isdir(save_dir): os.makedirs(save_dir)

                torch.save(self.decoder.state_dict(),
                           os.path.join(save_dir, 'decoder.pkl'))
                torch.save(self.encoder.state_dict(),
                           os.path.join(save_dir, 'encoder.pkl'))

